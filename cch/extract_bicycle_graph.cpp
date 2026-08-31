#include <routingkit/osm_graph_builder.h>
#include <routingkit/osm_profile.h>
#include <routingkit/id_mapper.h>
#include <routingkit/inverse_vector.h>
#include <routingkit/vector_io.h>

#include <chrono>
#include <cstring>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unordered_set>
#include <unordered_map>

using Clock = std::chrono::steady_clock;

namespace {
double elapsed_ms(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}

std::string path(const std::string& prefix, const char* suffix) {
    return prefix + suffix;
}

bool equals(const char* value, const char* expected) {
    return value != nullptr && std::strcmp(value, expected) == 0;
}

bool one_of(const char* value, std::initializer_list<const char*> values) {
    if (!value) return false;
    for (const char* candidate : values) if (equals(value, candidate)) return true;
    return false;
}

bool is_islo_bicycle_way(std::uint64_t, const RoutingKit::TagMap& tags) {
    const char* highway = tags["highway"];
    const char* route = tags["route"];
    const char* bicycle = tags["bicycle"];
    const char* access = tags["access"];
    const char* vehicle = tags["vehicle"];
    const char* foot = tags["foot"];
    const char* surface = tags["surface"];
    const bool bicycle_allowed = one_of(bicycle, {"yes", "designated", "official", "permissive"});
    const bool walkable = !one_of(foot, {"no", "private"}) && !equals(access, "private");
    if (equals(route, "ferry")) return bicycle_allowed;
    if (!highway || one_of(highway, {"motorway", "motorway_link", "construction", "proposed"})) {
        return false;
    }
    // A motorroad has motorway-like access rules. A physical shoulder is not
    // evidence of legal bicycle access, so omit it unless the OSM tag itself
    // is corrected in the source data.
    if (equals(tags["motorroad"], "yes")) return false;
    const bool forced_bridge_dismount = equals(bicycle, "no") && equals(tags["bridge"], "yes")
        && one_of(highway, {"primary", "secondary", "tertiary"});
    const bool walkable_connector = one_of(highway, {"footway", "pedestrian", "path"})
        && walkable && tags["sac_scale"] == nullptr
        && !one_of(surface, {"dirt", "earth", "ground", "mud", "sand", "grass", "woodchips"});
    if (one_of(bicycle, {"no", "private"}) && !forced_bridge_dismount && !walkable_connector) {
        return false;
    }
    if (one_of(access, {"no", "private"}) && !bicycle_allowed && !walkable_connector) return false;
    if (one_of(vehicle, {"no", "private"}) && !bicycle_allowed && !walkable_connector) return false;
    if (equals(highway, "steps")) return walkable;
    if (equals(highway, "elevator")) return walkable;
    if (equals(highway, "path") && !bicycle_allowed) {
        if (tags["sac_scale"] || tags["trail_visibility"] || tags["mtb:scale"]
            || equals(tags["informal"], "yes")) return false;
        if (!one_of(surface, {"paved", "asphalt", "concrete", "concrete:plates",
                             "concrete:lanes", "paving_stones", "sett",
                             "unhewn_cobblestone", "compacted", "fine_gravel"})) return false;
    }
    return one_of(highway, {
        "cycleway", "path", "track", "footway", "pedestrian", "steps", "elevator",
        "living_street", "residential", "service", "unclassified", "tertiary",
        "tertiary_link", "secondary", "secondary_link", "primary", "primary_link",
        "bicycle_road", "crossing", "escape"
    });
}

unsigned char way_flags(const RoutingKit::TagMap& tags) {
    unsigned char flags = 0;
    const char* highway = tags["highway"];
    const char* bicycle = tags["bicycle"];
    const bool friendly = equals(highway, "cycleway")
        || one_of(bicycle, {"designated", "official"})
        || equals(tags["bicycle_road"], "yes")
        || one_of(tags["cycleway"], {"track", "lane", "separate", "shoulder"})
        || one_of(tags["cycleway:left"], {"track", "lane", "separate", "shoulder"})
        || one_of(tags["cycleway:right"], {"track", "lane", "separate", "shoulder"})
        || one_of(tags["cycleway:both"], {"track", "lane", "separate", "shoulder"});
    if (friendly) flags |= 1 << 0;
    if (equals(highway, "steps")) flags |= 1 << 1;
    if (equals(highway, "elevator")) flags |= 1 << 2;
    if (equals(tags["bridge"], "yes")) flags |= 1 << 3;
    if (equals(bicycle, "dismount") || equals(highway, "steps") || equals(highway, "elevator")) flags |= 1 << 4;
    if (equals(tags["route"], "ferry")) flags |= 1 << 5;
    if (equals(tags["tunnel"], "yes")) flags |= 1 << 6;
    if (equals(tags["junction"], "roundabout")) flags |= 1 << 7;
    return flags;
}

unsigned char way_road_class(const RoutingKit::TagMap& tags) {
    const char* highway = tags["highway"];
    const char* bicycle = tags["bicycle"];
    const bool dedicated = equals(highway, "cycleway")
        || equals(tags["bicycle_road"], "yes");
    if (dedicated) return 2;
    const bool friendly = one_of(bicycle, {"designated", "official"})
        || one_of(tags["cycleway"], {"track", "lane", "separate", "shoulder",
                                     "shared_lane", "share_busway"})
        || one_of(tags["cycleway:left"], {"track", "lane", "separate", "shoulder",
                                          "shared_lane", "share_busway"})
        || one_of(tags["cycleway:right"], {"track", "lane", "separate", "shoulder",
                                           "shared_lane", "share_busway"})
        || one_of(tags["cycleway:both"], {"track", "lane", "separate", "shoulder",
                                          "shared_lane", "share_busway"});
    return friendly ? 1 : 0;
}

unsigned lane_count(const char* value) {
    if (!value) return 0;
    unsigned count = 0;
    while (*value >= '0' && *value <= '9') {
        count = count * 10 + static_cast<unsigned>(*value - '0');
        ++value;
    }
    return count;
}

unsigned char crossing_wait_for_way(const RoutingKit::TagMap& tags) {
    const unsigned lanes = std::max(
        lane_count(tags["lanes"]),
        lane_count(tags["lanes:forward"]) + lane_count(tags["lanes:backward"]));
    const char* highway = tags["highway"];
    if (lanes >= 8 || one_of(highway, {"trunk", "primary"})) return 135;
    if (lanes >= 4 || one_of(highway, {"secondary", "tertiary"})) return 75;
    return 45;
}
}

int main(int argc, char** argv) try {
    if (argc != 3) {
        std::cerr << "usage: " << argv[0] << " south-korea.osm.pbf output-prefix\n";
        return 2;
    }
    const std::string pbf = argv[1];
    const std::string prefix = argv[2];
    const auto started = Clock::now();
    auto log = [](const std::string& message) { std::cerr << "cch.extract " << message << '\n'; };
    std::unordered_set<std::uint64_t> elevator_nodes;
    std::unordered_map<std::uint64_t, unsigned char> crossing_nodes;
    std::unordered_map<std::uint64_t, std::string> named_interruption_nodes;
    auto mapping = RoutingKit::load_osm_id_mapping_from_pbf(
        pbf,
        [&](std::uint64_t osm_node_id, const RoutingKit::TagMap& tags) {
            const bool elevator = equals(tags["highway"], "elevator")
                || equals(tags["elevator"], "yes");
            if (elevator) elevator_nodes.insert(osm_node_id);
            if (elevator) {
                const char* name = tags["name:ko"] ? tags["name:ko"] : tags["name"];
                if (name && !one_of(name, {"엘리베이터", "승강기", "elevator", "Elevator"}))
                    named_interruption_nodes.emplace(osm_node_id, name);
            }
            const bool crossing = equals(tags["highway"], "crossing")
                || tags["crossing"] != nullptr;
            if (crossing) {
                const bool signals = equals(tags["crossing"], "traffic_signals")
                    || equals(tags["traffic_signals"], "yes");
                crossing_nodes[osm_node_id] = signals ? 2 : 1;
            }
            return elevator || crossing;
        },
        [](std::uint64_t osm_way_id, const RoutingKit::TagMap& tags) {
            return is_islo_bicycle_way(osm_way_id, tags);
        },
        log,
        false
    );
    std::vector<unsigned char> way_comfort(mapping.is_routing_way.population_count());
    std::vector<unsigned char> way_attribute_flags(mapping.is_routing_way.population_count());
    std::vector<unsigned char> way_road_classes(mapping.is_routing_way.population_count());
    std::vector<unsigned char> way_crossing_wait(mapping.is_routing_way.population_count());
    std::vector<unsigned char> node_flags(mapping.is_routing_node.population_count(), 0);
    std::vector<unsigned> node_label_id(mapping.is_routing_node.population_count(), 0);
    std::vector<unsigned> way_label_id(mapping.is_routing_way.population_count(), 0);
    std::vector<std::string> labels = {""};
    std::unordered_map<std::string, unsigned> label_id;
    auto intern_label = [&](const char* value) {
        if (!value || !*value) return 0u;
        const std::string label(value);
        const auto found = label_id.find(label);
        if (found != label_id.end()) return found->second;
        const unsigned id = static_cast<unsigned>(labels.size());
        labels.push_back(label);
        label_id.emplace(label, id);
        return id;
    };
    RoutingKit::IDMapper routing_node_mapper(mapping.is_routing_node);
    for (std::uint64_t osm_node_id : elevator_nodes) {
        const unsigned routing_node = routing_node_mapper.to_local(osm_node_id, RoutingKit::invalid_id);
        if (routing_node != RoutingKit::invalid_id) node_flags[routing_node] |= 1 << 0;
    }
    for (const auto& entry : crossing_nodes) {
        const unsigned routing_node = routing_node_mapper.to_local(entry.first, RoutingKit::invalid_id);
        if (routing_node != RoutingKit::invalid_id) node_flags[routing_node] |= entry.second << 1;
    }
    for (const auto& entry : named_interruption_nodes) {
        const unsigned routing_node = routing_node_mapper.to_local(entry.first, RoutingKit::invalid_id);
        if (routing_node != RoutingKit::invalid_id)
            node_label_id[routing_node] = intern_label(entry.second.c_str());
    }
    auto graph = RoutingKit::load_osm_routing_graph_from_pbf(
        pbf,
        mapping,
        [&](std::uint64_t osm_way_id, unsigned routing_way_id, const RoutingKit::TagMap& tags) {
            way_comfort[routing_way_id] = RoutingKit::get_osm_way_bicycle_comfort_level(
                osm_way_id, tags, nullptr);
            way_attribute_flags[routing_way_id] = way_flags(tags);
            way_road_classes[routing_way_id] = way_road_class(tags);
            way_crossing_wait[routing_way_id] = crossing_wait_for_way(tags);
            const unsigned char attributes = way_attribute_flags[routing_way_id];
            if ((attributes & ((1 << 1) | (1 << 2) | (1 << 3) | (1 << 4))) != 0) {
                const char* name = tags["bridge:name:ko"] ? tags["bridge:name:ko"]
                    : tags["bridge:name"] ? tags["bridge:name"]
                    : tags["name:ko"] ? tags["name:ko"] : tags["name"];
                way_label_id[routing_way_id] = intern_label(name);
            }
            return RoutingKit::get_osm_bicycle_direction_category(osm_way_id, tags, nullptr);
        },
        nullptr,
        log,
        false,
        RoutingKit::OSMRoadGeometry::uncompressed
    );
    mapping = RoutingKit::OSMRoutingIDMapping();
    const double decode_ms = elapsed_ms(started);

    std::vector<unsigned char> arc_comfort(graph.arc_count());
    std::vector<unsigned char> arc_flags(graph.arc_count());
    std::vector<unsigned char> arc_road_class(graph.arc_count());
    std::vector<unsigned char> arc_crossing_wait(graph.arc_count(), 0);
    std::vector<unsigned> arc_label_id(graph.arc_count(), 0);
    for (unsigned arc = 0; arc < graph.arc_count(); ++arc) {
        arc_comfort[arc] = way_comfort[graph.way[arc]];
        arc_flags[arc] = way_attribute_flags[graph.way[arc]];
        arc_road_class[arc] = way_road_classes[graph.way[arc]];
        arc_label_id[arc] = way_label_id[graph.way[arc]];
        const unsigned char crossing = (node_flags[graph.head[arc]] >> 1) & 0x3;
        if (crossing != 0) {
            arc_crossing_wait[arc] = crossing == 2
                ? std::max<unsigned char>(75, way_crossing_wait[graph.way[arc]])
                : way_crossing_wait[graph.way[arc]];
        }
    }

    // RoutingKit stores modelling points in directed-arc order. A normal
    // bidirectional road therefore contains the same geometry twice, reversed.
    // Canonicalize each OSM-way segment and let arcs reference it with one byte
    // indicating whether traversal reverses the stored point order.
    const auto tail = RoutingKit::invert_inverse_vector(graph.first_out);
    std::vector<unsigned> arc_order(graph.arc_count());
    for (unsigned arc = 0; arc < graph.arc_count(); ++arc) arc_order[arc] = arc;
    auto segment_key_less = [&](unsigned lhs, unsigned rhs) {
        if (graph.way[lhs] != graph.way[rhs]) return graph.way[lhs] < graph.way[rhs];
        const unsigned lhs_low = std::min(tail[lhs], graph.head[lhs]);
        const unsigned rhs_low = std::min(tail[rhs], graph.head[rhs]);
        if (lhs_low != rhs_low) return lhs_low < rhs_low;
        return std::max(tail[lhs], graph.head[lhs]) < std::max(tail[rhs], graph.head[rhs]);
    };
    std::sort(arc_order.begin(), arc_order.end(), segment_key_less);
    std::vector<unsigned> first_geometry = {0};
    std::vector<float> geometry_latitude;
    std::vector<float> geometry_longitude;
    geometry_latitude.reserve(graph.modelling_node_latitude.size() / 2 + 1);
    geometry_longitude.reserve(graph.modelling_node_longitude.size() / 2 + 1);
    std::vector<unsigned> arc_geometry_id(graph.arc_count());
    std::vector<unsigned char> arc_geometry_reversed(graph.arc_count(), 0);
    std::size_t group_begin = 0;
    while (group_begin < arc_order.size()) {
        std::size_t group_end = group_begin + 1;
        while (group_end < arc_order.size()
               && !segment_key_less(arc_order[group_begin], arc_order[group_end])
               && !segment_key_less(arc_order[group_end], arc_order[group_begin])) {
            ++group_end;
        }
        unsigned canonical_arc = arc_order[group_begin];
        for (std::size_t index = group_begin; index < group_end; ++index) {
            const unsigned candidate = arc_order[index];
            if (!graph.is_arc_antiparallel_to_way[candidate]) {
                canonical_arc = candidate;
                break;
            }
        }
        const unsigned geometry_id = static_cast<unsigned>(first_geometry.size() - 1);
        const unsigned geometry_begin = graph.first_modelling_node[canonical_arc];
        const unsigned geometry_end = graph.first_modelling_node[canonical_arc + 1];
        geometry_latitude.insert(
            geometry_latitude.end(),
            graph.modelling_node_latitude.begin() + geometry_begin,
            graph.modelling_node_latitude.begin() + geometry_end);
        geometry_longitude.insert(
            geometry_longitude.end(),
            graph.modelling_node_longitude.begin() + geometry_begin,
            graph.modelling_node_longitude.begin() + geometry_end);
        first_geometry.push_back(static_cast<unsigned>(geometry_latitude.size()));
        for (std::size_t index = group_begin; index < group_end; ++index) {
            const unsigned arc = arc_order[index];
            arc_geometry_id[arc] = geometry_id;
            arc_geometry_reversed[arc] = tail[arc] == graph.head[canonical_arc]
                && graph.head[arc] == tail[canonical_arc];
        }
        group_begin = group_end;
    }

    RoutingKit::save_vector(path(prefix, ".first_out.u32"), graph.first_out);
    RoutingKit::save_vector(path(prefix, ".head.u32"), graph.head);
    RoutingKit::save_vector(path(prefix, ".distance.u32"), graph.geo_distance);
    RoutingKit::save_vector(path(prefix, ".latitude.f32"), graph.latitude);
    RoutingKit::save_vector(path(prefix, ".longitude.f32"), graph.longitude);
    RoutingKit::save_vector(path(prefix, ".comfort.u8"), arc_comfort);
    RoutingKit::save_vector(path(prefix, ".flags.u8"), arc_flags);
    RoutingKit::save_vector(path(prefix, ".road_class.u8"), arc_road_class);
    RoutingKit::save_vector(path(prefix, ".node_flags.u8"), node_flags);
    RoutingKit::save_vector(path(prefix, ".crossing_wait.u8"), arc_crossing_wait);
    RoutingKit::save_vector(path(prefix, ".node_label_id.u32"), node_label_id);
    RoutingKit::save_vector(path(prefix, ".arc_label_id.u32"), arc_label_id);
    RoutingKit::save_vector(path(prefix, ".labels.txt"), labels);
    RoutingKit::save_vector(path(prefix, ".first_geometry.u32"), first_geometry);
    RoutingKit::save_vector(path(prefix, ".geometry_latitude.f32"), geometry_latitude);
    RoutingKit::save_vector(path(prefix, ".geometry_longitude.f32"), geometry_longitude);
    RoutingKit::save_vector(path(prefix, ".arc_geometry_id.u32"), arc_geometry_id);
    RoutingKit::save_vector(path(prefix, ".arc_geometry_reversed.u8"), arc_geometry_reversed);

    std::uint64_t distance_sum = 0;
    for (unsigned distance : graph.geo_distance) distance_sum += distance;
    std::cout << "{\n"
              << "  \"source\": \"" << pbf << "\",\n"
              << "  \"nodes\": " << graph.node_count() << ",\n"
              << "  \"arcs\": " << graph.arc_count() << ",\n"
              << "  \"directedGeometryPoints\": " << graph.modelling_node_latitude.size() << ",\n"
              << "  \"uniqueGeometrySegments\": " << first_geometry.size() - 1 << ",\n"
              << "  \"uniqueGeometryPoints\": " << geometry_latitude.size() << ",\n"
              << "  \"directedDistanceMeters\": " << distance_sum << ",\n"
              << "  \"labels\": " << labels.size() - 1 << ",\n"
              << "  \"decodeMs\": " << decode_ms << ",\n"
              << "  \"totalMs\": " << elapsed_ms(started) << "\n"
              << "}\n";
    return graph.node_count() == 0 || graph.arc_count() == 0 ? 3 : 0;
} catch (const std::exception& error) {
    std::cerr << "CCH bicycle extraction failed: " << error.what() << '\n';
    return 1;
}
