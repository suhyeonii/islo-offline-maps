#include <routingkit/constants.h>
#include <routingkit/customizable_contraction_hierarchy.h>
#include <routingkit/inverse_vector.h>
#include <routingkit/nested_dissection.h>
#include <routingkit/osm_simple.h>
#include <routingkit/vector_io.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

using Clock = std::chrono::steady_clock;

namespace {
double elapsed_ms(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

std::string path(const std::string& prefix, const char* suffix) { return prefix + suffix; }

struct DisjointSet {
    std::vector<unsigned> parent;
    std::vector<unsigned> size;
    explicit DisjointSet(unsigned count) : parent(count), size(count, 1) {
        for (unsigned i = 0; i < count; ++i) parent[i] = i;
    }
    unsigned root(unsigned node) {
        unsigned value = node;
        while (parent[value] != value) value = parent[value];
        while (parent[node] != node) {
            const unsigned next = parent[node];
            parent[node] = value;
            node = next;
        }
        return value;
    }
    void join(unsigned lhs, unsigned rhs) {
        lhs = root(lhs);
        rhs = root(rhs);
        if (lhs == rhs) return;
        if (size[lhs] < size[rhs]) std::swap(lhs, rhs);
        parent[rhs] = lhs;
        size[lhs] += size[rhs];
    }
};

unsigned scaled_weight(unsigned meters, double factor) {
    const double value = std::max(1.0, meters * 10.0 * factor);
    return static_cast<unsigned>(std::min<double>(RoutingKit::inf_weight - 1, value));
}
}

int main(int argc, char** argv) try {
    if (argc < 2 || argc > 3) {
        std::cerr << "usage: " << argv[0] << " extracted-array-prefix [cch-output-prefix]\n";
        return 2;
    }
    const std::string prefix = argv[1];
    const std::string output_prefix = argc == 3 ? argv[2] : "";
    const auto total_started = Clock::now();
    const auto load_started = Clock::now();
    auto first_out = RoutingKit::load_vector<unsigned>(path(prefix, ".first_out.u32"));
    auto head = RoutingKit::load_vector<unsigned>(path(prefix, ".head.u32"));
    auto distance = RoutingKit::load_vector<unsigned>(path(prefix, ".distance.u32"));
    auto latitude = RoutingKit::load_vector<float>(path(prefix, ".latitude.f32"));
    auto longitude = RoutingKit::load_vector<float>(path(prefix, ".longitude.f32"));
    auto comfort = RoutingKit::load_vector<unsigned char>(path(prefix, ".comfort.u8"));
    auto flags = RoutingKit::load_vector<unsigned char>(path(prefix, ".flags.u8"));
    auto road_class = RoutingKit::load_vector<unsigned char>(path(prefix, ".road_class.u8"));
    auto official_route = RoutingKit::load_vector<unsigned char>(path(prefix, ".official_route.u8"));
    auto elevation_gain = RoutingKit::load_vector<std::uint16_t>(path(prefix, ".elevation_gain.u16"));
    auto crossing_wait = RoutingKit::load_vector<unsigned char>(path(prefix, ".crossing_wait.u8"));
    const double load_ms = elapsed_ms(load_started);
    if (first_out.empty() || first_out.back() != head.size() || head.size() != distance.size()
        || head.size() != comfort.size() || head.size() != flags.size()
        || head.size() != road_class.size()
        || head.size() != official_route.size() || head.size() != elevation_gain.size()
        || head.size() != crossing_wait.size()
        || latitude.size() + 1 != first_out.size()
        || longitude.size() != latitude.size()) {
        throw std::runtime_error("array sizes are inconsistent");
    }
    const unsigned node_count = static_cast<unsigned>(latitude.size());
    auto tail = RoutingKit::invert_inverse_vector(first_out);

    DisjointSet components(node_count);
    for (std::size_t arc = 0; arc < head.size(); ++arc) components.join(tail[arc], head[arc]);
    unsigned weak_component_count = 0;
    unsigned largest_root = RoutingKit::invalid_id;
    unsigned largest_nodes = 0;
    for (unsigned node = 0; node < node_count; ++node) {
        if (components.parent[node] != node) continue;
        ++weak_component_count;
        if (components.size[node] > largest_nodes) {
            largest_nodes = components.size[node];
            largest_root = node;
        }
    }
    std::vector<unsigned> largest_component;
    largest_component.reserve(largest_nodes);
    for (unsigned node = 0; node < node_count; ++node) {
        if (components.root(node) == largest_root) largest_component.push_back(node);
    }

    const auto order_started = Clock::now();
    auto order = RoutingKit::compute_nested_node_dissection_order_using_inertial_flow(
        node_count, tail, head, latitude, longitude);
    const double order_ms = elapsed_ms(order_started);
    const auto topology_started = Clock::now();
    RoutingKit::CustomizableContractionHierarchy cch(order, tail, head);
    const double topology_ms = elapsed_ms(topology_started);

    const unsigned min_comfort = RoutingKit::get_min_bicycle_comfort_level();
    const unsigned max_comfort = RoutingKit::get_max_bicycle_comfort_level();
    const unsigned comfort_span = std::max(1u, max_comfort - min_comfort);
    std::vector<unsigned> bicycle_weight(head.size());
    std::vector<unsigned> shortest_weight(head.size());
    std::vector<unsigned> flat_weight(head.size());
    // The normal three recommendations must never traverse stairs.  A separate
    // fallback metric retains them only for destinations that are otherwise
    // disconnected, so a large penalty can never compete with a valid detour.
    std::vector<unsigned> stairs_fallback_weight(head.size());
    std::uint64_t official_arc_count = 0;
    std::uint64_t interruption_arc_count = 0;
    for (std::size_t arc = 0; arc < head.size(); ++arc) {
        const double normalized = static_cast<double>(comfort[arc] - min_comfort) / comfort_span;
        const bool stairs = (flags[arc] & (1 << 1)) != 0;
        shortest_weight[arc] = stairs
            ? RoutingKit::inf_weight : scaled_weight(distance[arc], 1.0);
        double bicycle_factor = 1.65 - normalized * 1.15;
        if (road_class[arc] >= 2) bicycle_factor *= 0.55;
        else if (road_class[arc] == 1) bicycle_factor *= 0.78;
        if (official_route[arc] != 0) {
            bicycle_factor *= 0.48;
            ++official_arc_count;
        }
        const bool interruption = (flags[arc] & ((1 << 1) | (1 << 2) | (1 << 4))) != 0;
        if (interruption) {
            bicycle_factor += 0.80;
            ++interruption_arc_count;
        }
        const double crossing_equivalent_meters = crossing_wait[arc] * 4.2;
        const unsigned bicycle_cost = static_cast<unsigned>(std::min<double>(
            RoutingKit::inf_weight - 1,
            std::max(1.0, (distance[arc] * bicycle_factor + crossing_equivalent_meters) * 10.0)));
        bicycle_weight[arc] = stairs ? RoutingKit::inf_weight : bicycle_cost;
        // This metric is queried only after all stair-free profiles report no
        // path.  Keep its cost finite so a true last-resort staircase can be
        // reconstructed and announced.
        // The iOS mmap query reserves values above roughly 1.07B as infinity.
        // Keep the fallback below that sentinel (20,000 km equivalent) so an
        // actually isolated destination can still be connected, while the
        // normal profiles above remain mathematically stair-free.
        constexpr unsigned stair_last_resort_weight = 200'000'000u;
        stairs_fallback_weight[arc] = stairs ? stair_last_resort_weight : bicycle_cost;
        const double flat_distance = distance[arc] * bicycle_factor
            + static_cast<double>(elevation_gain[arc]) * 22.0
            + crossing_equivalent_meters + (interruption ? 180.0 : 0.0);
        flat_weight[arc] = stairs ? RoutingKit::inf_weight : static_cast<unsigned>(std::min<double>(
            RoutingKit::inf_weight - 1, std::max(1.0, flat_distance * 10.0)));
    }
    const auto customize_started = Clock::now();
    RoutingKit::CustomizableContractionHierarchyMetric bicycle(cch, bicycle_weight);
    RoutingKit::CustomizableContractionHierarchyMetric shortest(cch, shortest_weight);
    RoutingKit::CustomizableContractionHierarchyMetric flat(cch, flat_weight);
    RoutingKit::CustomizableContractionHierarchyMetric stairs_fallback(cch, stairs_fallback_weight);
    bicycle.customize();
    shortest.customize();
    flat.customize();
    stairs_fallback.customize();
    const double customize_ms = elapsed_ms(customize_started);

    RoutingKit::CustomizableContractionHierarchyQuery query(bicycle);
    std::mt19937 rng(0x49534C4F);
    std::uniform_int_distribution<std::size_t> largest_distribution(0, largest_component.size() - 1);
    std::vector<double> query_ms;
    unsigned reachable = 0;
    for (unsigned i = 0; i < 100; ++i) {
        const unsigned source = largest_component[largest_distribution(rng)];
        const unsigned target = largest_component[largest_distribution(rng)];
        const auto started = Clock::now();
        query.reset().add_source(source).add_target(target).run();
        query_ms.push_back(elapsed_ms(started));
        if (query.get_distance() != RoutingKit::inf_weight) ++reachable;
    }
    std::sort(query_ms.begin(), query_ms.end());

    const std::uint64_t topology_bytes =
        (cch.order.size() + cch.rank.size() + cch.elimination_tree_parent.size()
         + cch.up_first_out.size() + cch.up_head.size() + cch.up_tail.size()
         + cch.down_first_out.size() + cch.down_head.size() + cch.down_to_up.size()
         + cch.input_arc_to_cch_arc.size() + cch.forward_input_arc_of_cch.size()
         + cch.backward_input_arc_of_cch.size() + cch.first_extra_forward_input_arc_of_cch.size()
         + cch.first_extra_backward_input_arc_of_cch.size()
         + cch.extra_forward_input_arc_of_cch.size() + cch.extra_backward_input_arc_of_cch.size())
        * sizeof(unsigned);
    const std::uint64_t four_metric_bytes =
        (bicycle.forward.size() + bicycle.backward.size()
         + shortest.forward.size() + shortest.backward.size()
         + flat.forward.size() + flat.backward.size()
         + stairs_fallback.forward.size() + stairs_fallback.backward.size()) * sizeof(unsigned);

    if (!output_prefix.empty()) {
        auto save = [&](const char* suffix, const std::vector<unsigned>& values) {
            RoutingKit::save_vector(path(output_prefix, suffix), values);
        };
        save(".order.u32", cch.order);
        save(".rank.u32", cch.rank);
        save(".elimination_parent.u32", cch.elimination_tree_parent);
        save(".up_first_out.u32", cch.up_first_out);
        save(".up_head.u32", cch.up_head);
        save(".up_tail.u32", cch.up_tail);
        save(".down_first_out.u32", cch.down_first_out);
        save(".down_head.u32", cch.down_head);
        save(".down_to_up.u32", cch.down_to_up);
        save(".input_arc_to_cch_arc.u32", cch.input_arc_to_cch_arc);
        RoutingKit::save_bit_vector(
            path(output_prefix, ".is_input_arc_upward.bits"), cch.is_input_arc_upward);
        RoutingKit::save_bit_vector(
            path(output_prefix, ".has_input_arc.bits"), cch.does_cch_arc_have_input_arc);
        save(".forward_input_arc.u32", cch.forward_input_arc_of_cch);
        save(".backward_input_arc.u32", cch.backward_input_arc_of_cch);
        RoutingKit::save_bit_vector(
            path(output_prefix, ".has_extra_input_arc.bits"),
            cch.does_cch_arc_have_extra_input_arc);
        save(".first_extra_forward_input_arc.u32", cch.first_extra_forward_input_arc_of_cch);
        save(".first_extra_backward_input_arc.u32", cch.first_extra_backward_input_arc_of_cch);
        save(".extra_forward_input_arc.u32", cch.extra_forward_input_arc_of_cch);
        save(".extra_backward_input_arc.u32", cch.extra_backward_input_arc_of_cch);
        save(".bicycle.forward.u32", bicycle.forward);
        save(".bicycle.backward.u32", bicycle.backward);
        save(".bicycle.input_weight.u32", bicycle_weight);
        save(".shortest.forward.u32", shortest.forward);
        save(".shortest.backward.u32", shortest.backward);
        save(".shortest.input_weight.u32", shortest_weight);
        save(".flat.forward.u32", flat.forward);
        save(".flat.backward.u32", flat.backward);
        save(".flat.input_weight.u32", flat_weight);
        save(".stairs_fallback.forward.u32", stairs_fallback.forward);
        save(".stairs_fallback.backward.u32", stairs_fallback.backward);
        save(".stairs_fallback.input_weight.u32", stairs_fallback_weight);
    }

    std::cout << "{\n"
              << "  \"prefix\": \"" << prefix << "\",\n"
              << "  \"outputPrefix\": \"" << output_prefix << "\",\n"
              << "  \"activeNodes\": " << node_count << ",\n"
              << "  \"inputArcs\": " << head.size() << ",\n"
              << "  \"cchArcs\": " << cch.cch_arc_count() << ",\n"
              << "  \"weakComponentCount\": " << weak_component_count << ",\n"
              << "  \"largestWeakComponentNodes\": " << largest_nodes << ",\n"
              << "  \"largestWeakComponentRatio\": " << static_cast<double>(largest_nodes) / node_count << ",\n"
              << "  \"reachableArcPairs\": 50,\n"
              << "  \"reachableRandomQueries\": " << reachable << ",\n"
              << "  \"officialRouteArcs\": " << official_arc_count << ",\n"
              << "  \"interruptionArcs\": " << interruption_arc_count << ",\n"
              << "  \"queryP50Ms\": " << query_ms[49] << ",\n"
              << "  \"queryP95Ms\": " << query_ms[94] << ",\n"
              << "  \"queryMaxMs\": " << query_ms.back() << ",\n"
              << "  \"estimatedTopologyBytes\": " << topology_bytes << ",\n"
              << "  \"estimatedFourMetricBytes\": " << four_metric_bytes << ",\n"
              << "  \"loadMs\": " << load_ms << ",\n"
              << "  \"orderMs\": " << order_ms << ",\n"
              << "  \"topologyMs\": " << topology_ms << ",\n"
              << "  \"customizeThreeMetricsMs\": " << customize_ms << ",\n"
              << "  \"totalMs\": " << elapsed_ms(total_started) << "\n"
              << "}\n";
    return 0;
} catch (const std::exception& error) {
    std::cerr << "CCH array benchmark failed: " << error.what() << '\n';
    return 1;
}
