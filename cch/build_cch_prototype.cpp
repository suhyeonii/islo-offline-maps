#include <routingkit/constants.h>
#include <routingkit/customizable_contraction_hierarchy.h>
#include <routingkit/nested_dissection.h>

#include <sqlite3.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

using Clock = std::chrono::steady_clock;

namespace {

struct SQLiteDB {
    sqlite3* handle = nullptr;
    explicit SQLiteDB(const char* path) {
        if (sqlite3_open_v2(path, &handle, SQLITE_OPEN_READONLY, nullptr) != SQLITE_OK) {
            throw std::runtime_error(sqlite3_errmsg(handle));
        }
    }
    ~SQLiteDB() { if (handle) sqlite3_close(handle); }
};

struct Statement {
    sqlite3_stmt* handle = nullptr;
    Statement(sqlite3* db, const char* sql) {
        if (sqlite3_prepare_v2(db, sql, -1, &handle, nullptr) != SQLITE_OK) {
            throw std::runtime_error(sqlite3_errmsg(db));
        }
    }
    ~Statement() { if (handle) sqlite3_finalize(handle); }
};

struct ComponentSummary {
    unsigned root;
    unsigned nodes;
    float south = std::numeric_limits<float>::max();
    float west = std::numeric_limits<float>::max();
    float north = std::numeric_limits<float>::lowest();
    float east = std::numeric_limits<float>::lowest();
};

double elapsed_ms(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

unsigned bounded_weight(double value) {
    if (!std::isfinite(value) || value < 0) return RoutingKit::inf_weight;
    constexpr double max_value = static_cast<double>(RoutingKit::inf_weight - 1);
    return static_cast<unsigned>(std::min(max_value, std::max(1.0, std::round(value))));
}

template<class T>
void write_json_array_stat(const char* name, const std::vector<T>& values, bool comma = true) {
    std::cout << "  \"" << name << "\": " << values.size();
    if (comma) std::cout << ',';
    std::cout << '\n';
}

} // namespace

int main(int argc, char** argv) try {
    if (argc < 2 || argc > 3) {
        std::cerr << "usage: " << argv[0] << " routing.sqlite [max_edges]\n";
        return 2;
    }
    const char* db_path = argv[1];
    const std::uint64_t max_edges = argc == 3 ? std::strtoull(argv[2], nullptr, 10) : 0;
    const auto total_start = Clock::now();
    SQLiteDB db(db_path);

    std::vector<std::int64_t> osm_node_ids;
    std::vector<unsigned> tail, head;
    std::vector<unsigned> bicycle_weight, shortest_weight, flat_weight;
    std::unordered_map<std::int64_t, unsigned> dense_id;
    std::vector<unsigned> component_parent;
    std::vector<unsigned> component_size;
    if (max_edges) dense_id.reserve(static_cast<std::size_t>(max_edges));

    const auto load_edges_start = Clock::now();
    Statement edges(db.handle,
        "SELECT src,dst,meters,cost,is_cycleway,is_dedicated_cycleway,is_dismount,interruption_kind,"
        "is_bridge,crossing_wait_seconds FROM edges ORDER BY src,dst");
    auto node_for = [&](std::int64_t osm_id) -> unsigned {
        auto found = dense_id.find(osm_id);
        if (found != dense_id.end()) return found->second;
        if (osm_node_ids.size() >= RoutingKit::invalid_id) {
            throw std::runtime_error("too many active nodes for UInt32 CCH");
        }
        unsigned id = static_cast<unsigned>(osm_node_ids.size());
        dense_id.emplace(osm_id, id);
        osm_node_ids.push_back(osm_id);
        component_parent.push_back(id);
        component_size.push_back(1);
        return id;
    };
    auto component_root = [&](unsigned node) {
        unsigned root = node;
        while (component_parent[root] != root) root = component_parent[root];
        while (component_parent[node] != node) {
            const unsigned next = component_parent[node];
            component_parent[node] = root;
            node = next;
        }
        return root;
    };
    auto join_components = [&](unsigned lhs, unsigned rhs) {
        lhs = component_root(lhs);
        rhs = component_root(rhs);
        if (lhs == rhs) return;
        if (component_size[lhs] < component_size[rhs]) std::swap(lhs, rhs);
        component_parent[rhs] = lhs;
        component_size[lhs] += component_size[rhs];
    };

    while (sqlite3_step(edges.handle) == SQLITE_ROW) {
        if (max_edges && tail.size() >= max_edges) break;
        const auto src = sqlite3_column_int64(edges.handle, 0);
        const auto dst = sqlite3_column_int64(edges.handle, 1);
        const double meters = sqlite3_column_double(edges.handle, 2);
        const double cost = sqlite3_column_double(edges.handle, 3);
        const bool cycleway = sqlite3_column_int(edges.handle, 4) != 0;
        const bool dedicated_cycleway = sqlite3_column_int(edges.handle, 5) != 0;
        const bool dismount = sqlite3_column_int(edges.handle, 6) != 0;
        const int interruption = sqlite3_column_int(edges.handle, 7);
        const bool bridge = sqlite3_column_int(edges.handle, 8) != 0;
        const int crossing_wait = sqlite3_column_int(edges.handle, 9);
        const unsigned tail_node = node_for(src);
        const unsigned head_node = node_for(dst);
        tail.push_back(tail_node);
        head.push_back(head_node);
        join_components(tail_node, head_node);
        // 계단은 기본 자전거 경로에서 사실상 통과 불가로 처리합니다. CCH의
        // topology에는 남겨 두어 다른 접근이 전혀 없는 목적지만 최후 수단으로
        // 연결할 수 있지만, 일반 도로 우회와 경쟁하지 못하게 합니다.
        constexpr double stair_last_resort_cost = 50'000.0;
        const bool stairs = interruption == 2;
        shortest_weight.push_back(bounded_weight((stairs ? stair_last_resort_cost : meters) * 10.0));
        bicycle_weight.push_back(bounded_weight((stairs ? stair_last_resort_cost : cost) * 10.0));
        double flat_cost = cost;
        if (stairs) flat_cost = stair_last_resort_cost;
        if (dedicated_cycleway) flat_cost *= 0.72;
        else if (cycleway) flat_cost *= 0.82;
        if (dismount) flat_cost *= 1.8;
        if (interruption != 0) flat_cost += 45.0;
        if (bridge) flat_cost *= 1.02;
        flat_cost += std::max(0, crossing_wait);
        flat_weight.push_back(bounded_weight(flat_cost * 10.0));
    }
    const double load_edges_ms = elapsed_ms(load_edges_start);

    std::vector<float> latitude(osm_node_ids.size(), 0.0f);
    std::vector<float> longitude(osm_node_ids.size(), 0.0f);
    std::vector<unsigned char> coordinate_found(osm_node_ids.size(), 0);
    const auto load_nodes_start = Clock::now();
    Statement nodes(db.handle, "SELECT id,lat,lon FROM nodes");
    std::size_t coordinates_loaded = 0;
    while (sqlite3_step(nodes.handle) == SQLITE_ROW) {
        const auto osm_id = sqlite3_column_int64(nodes.handle, 0);
        auto found = dense_id.find(osm_id);
        if (found == dense_id.end()) continue;
        const unsigned id = found->second;
        latitude[id] = static_cast<float>(sqlite3_column_double(nodes.handle, 1));
        longitude[id] = static_cast<float>(sqlite3_column_double(nodes.handle, 2));
        coordinate_found[id] = 1;
        ++coordinates_loaded;
    }
    const double load_nodes_ms = elapsed_ms(load_nodes_start);
    if (coordinates_loaded != osm_node_ids.size()) {
        throw std::runtime_error("edge endpoint missing from nodes table");
    }
    dense_id.clear();
    dense_id.rehash(0);

    std::size_t weak_component_count = 0;
    unsigned largest_component_root = RoutingKit::invalid_id;
    unsigned largest_component_nodes = 0;
    for (unsigned node = 0; node < component_parent.size(); ++node) {
        if (component_parent[node] != node) continue;
        ++weak_component_count;
        if (component_size[node] > largest_component_nodes) {
            largest_component_nodes = component_size[node];
            largest_component_root = node;
        }
    }
    std::vector<unsigned> largest_component;
    largest_component.reserve(largest_component_nodes);
    for (unsigned node = 0; node < component_parent.size(); ++node) {
        if (component_root(node) == largest_component_root) largest_component.push_back(node);
    }
    std::vector<ComponentSummary> largest_components;
    largest_components.reserve(weak_component_count);
    for (unsigned node = 0; node < component_parent.size(); ++node) {
        if (component_parent[node] == node) {
            largest_components.push_back({node, component_size[node]});
        }
    }
    std::sort(largest_components.begin(), largest_components.end(),
        [](const ComponentSummary& lhs, const ComponentSummary& rhs) {
            return lhs.nodes > rhs.nodes;
        });
    if (largest_components.size() > 20) largest_components.resize(20);
    std::unordered_map<unsigned, std::size_t> reported_component;
    reported_component.reserve(largest_components.size());
    for (std::size_t i = 0; i < largest_components.size(); ++i) {
        reported_component.emplace(largest_components[i].root, i);
    }
    for (unsigned node = 0; node < component_parent.size(); ++node) {
        auto reported = reported_component.find(component_root(node));
        if (reported == reported_component.end()) continue;
        auto& summary = largest_components[reported->second];
        summary.south = std::min(summary.south, latitude[node]);
        summary.west = std::min(summary.west, longitude[node]);
        summary.north = std::max(summary.north, latitude[node]);
        summary.east = std::max(summary.east, longitude[node]);
    }

    const auto order_start = Clock::now();
    auto order = RoutingKit::compute_nested_node_dissection_order_using_inertial_flow(
        static_cast<unsigned>(osm_node_ids.size()), tail, head, latitude, longitude);
    const double order_ms = elapsed_ms(order_start);

    const auto topology_start = Clock::now();
    RoutingKit::CustomizableContractionHierarchy cch(order, tail, head);
    const double topology_ms = elapsed_ms(topology_start);

    const auto customize_start = Clock::now();
    RoutingKit::CustomizableContractionHierarchyMetric bicycle(cch, bicycle_weight);
    RoutingKit::CustomizableContractionHierarchyMetric shortest(cch, shortest_weight);
    RoutingKit::CustomizableContractionHierarchyMetric flat(cch, flat_weight);
    bicycle.customize();
    shortest.customize();
    flat.customize();
    const double customize_ms = elapsed_ms(customize_start);

    RoutingKit::CustomizableContractionHierarchyQuery query(bicycle);
    std::mt19937 rng(0x49534C4F);
    std::uniform_int_distribution<unsigned> node_distribution(0, cch.node_count() - 1);
    std::uniform_int_distribution<std::size_t> largest_component_distribution(
        0, largest_component.size() - 1);
    std::vector<double> query_ms;
    unsigned reachable_random = 0;
    unsigned reachable_arc_pairs = 0;
    for (unsigned i = 0; i < 100; ++i) {
        unsigned source;
        unsigned target;
        if (i < 50) {
            const std::size_t arc = static_cast<std::size_t>(rng()) % tail.size();
            source = tail[arc];
            target = head[arc];
        } else {
            source = largest_component[largest_component_distribution(rng)];
            target = largest_component[largest_component_distribution(rng)];
        }
        const auto query_start = Clock::now();
        query.reset().add_source(source).add_target(target).run();
        query_ms.push_back(elapsed_ms(query_start));
        if (query.get_distance() != RoutingKit::inf_weight) {
            if (i < 50) ++reachable_arc_pairs;
            else ++reachable_random;
        }
    }
    std::sort(query_ms.begin(), query_ms.end());

    std::cout << "{\n";
    std::cout << "  \"database\": \"" << db_path << "\",\n";
    std::cout << "  \"edgeLimit\": " << max_edges << ",\n";
    std::cout << "  \"activeNodes\": " << cch.node_count() << ",\n";
    std::cout << "  \"inputArcs\": " << cch.input_arc_count() << ",\n";
    std::cout << "  \"cchArcs\": " << cch.cch_arc_count() << ",\n";
    std::cout << "  \"weakComponentCount\": " << weak_component_count << ",\n";
    std::cout << "  \"largestWeakComponentNodes\": " << largest_component_nodes << ",\n";
    std::cout << "  \"largestWeakComponentRatio\": "
              << static_cast<double>(largest_component_nodes) / cch.node_count() << ",\n";
    std::cout << "  \"largestWeakComponents\": [\n";
    for (std::size_t i = 0; i < largest_components.size(); ++i) {
        const auto& component = largest_components[i];
        std::cout << "    {\"nodes\": " << component.nodes
                  << ", \"south\": " << component.south
                  << ", \"west\": " << component.west
                  << ", \"north\": " << component.north
                  << ", \"east\": " << component.east << '}';
        if (i + 1 != largest_components.size()) std::cout << ',';
        std::cout << '\n';
    }
    std::cout << "  ],\n";
    std::cout << "  \"reachableArcPairs\": " << reachable_arc_pairs << ",\n";
    std::cout << "  \"reachableRandomQueries\": " << reachable_random << ",\n";
    std::cout << "  \"queryP50Ms\": " << query_ms[49] << ",\n";
    std::cout << "  \"queryP95Ms\": " << query_ms[94] << ",\n";
    std::cout << "  \"queryMaxMs\": " << query_ms.back() << ",\n";
    std::cout << "  \"loadEdgesMs\": " << load_edges_ms << ",\n";
    std::cout << "  \"loadNodesMs\": " << load_nodes_ms << ",\n";
    std::cout << "  \"orderMs\": " << order_ms << ",\n";
    std::cout << "  \"topologyMs\": " << topology_ms << ",\n";
    std::cout << "  \"customizeThreeMetricsMs\": " << customize_ms << ",\n";
    std::cout << "  \"totalMs\": " << elapsed_ms(total_start) << "\n";
    std::cout << "}\n";
    return reachable_arc_pairs != 50 ? 3 : 0;
} catch (const std::exception& error) {
    std::cerr << "CCH prototype failed: " << error.what() << '\n';
    return 1;
}
