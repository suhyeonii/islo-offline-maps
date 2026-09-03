#include <routingkit/constants.h>
#include <routingkit/customizable_contraction_hierarchy.h>
#include <routingkit/inverse_vector.h>
#include <routingkit/nested_dissection.h>
#include <routingkit/vector_io.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

struct Coordinate { double latitude; double longitude; };

std::string path(const std::string& prefix, const char* suffix) { return prefix + suffix; }

double distance_meters(const Coordinate& a, const Coordinate& b) {
    constexpr double radians = 0.017453292519943295;
    constexpr double earth_radius = 6371000.0;
    const double d_lat = (b.latitude - a.latitude) * radians;
    const double d_lon = (b.longitude - a.longitude) * radians;
    const double h = std::sin(d_lat / 2.0) * std::sin(d_lat / 2.0)
        + std::cos(a.latitude * radians) * std::cos(b.latitude * radians)
        * std::sin(d_lon / 2.0) * std::sin(d_lon / 2.0);
    return 2.0 * earth_radius * std::asin(std::min(1.0, std::sqrt(h)));
}

std::uint64_t grid_key(int latitude_cell, int longitude_cell) {
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(latitude_cell)) << 32)
        | static_cast<std::uint32_t>(longitude_cell);
}

int cell(double value) { return static_cast<int>(std::floor(value * 100.0)); }

std::vector<Coordinate> read_csv(const std::string& filename, unsigned route_id) {
    std::ifstream input(filename);
    if (!input) throw std::runtime_error("cannot open " + filename);
    std::string header;
    std::getline(input, header);
    std::vector<Coordinate> result;
    for (std::string line; std::getline(input, line); ) {
        std::stringstream stream(line);
        std::string sequence, route, latitude, longitude;
        if (!std::getline(stream, sequence, ',') || !std::getline(stream, route, ',')
            || !std::getline(stream, latitude, ',') || !std::getline(stream, longitude, ',')) continue;
        if (static_cast<unsigned>(std::stoul(route)) != route_id) continue;
        result.push_back({std::stod(latitude), std::stod(longitude)});
    }
    if (result.size() < 2) throw std::runtime_error("official route has fewer than two points");
    return result;
}

void write_json(const std::string& filename, const std::vector<std::vector<Coordinate>>& segments) {
    std::ofstream output(filename);
    if (!output) throw std::runtime_error("cannot write " + filename);
    output << "[\n" << std::setprecision(8);
    for (std::size_t segment_index = 0; segment_index < segments.size(); ++segment_index) {
        const auto& points = segments[segment_index];
        output << "  [\n";
        for (std::size_t i = 0; i < points.size(); ++i) {
            output << "    {\"latitude\": " << points[i].latitude
                   << ", \"longitude\": " << points[i].longitude << "}";
            output << (i + 1 == points.size() ? "\n" : ",\n");
        }
        output << "  ]" << (segment_index + 1 == segments.size() ? "\n" : ",\n");
    }
    output << "]\n";
}

} // namespace

int main(int argc, char** argv) try {
    if (argc != 6) {
        std::cerr << "usage: " << argv[0]
                  << " graph-prefix cch-metric-prefix official-cycle-routes.csv route-id output-points.json\n";
        return 2;
    }
    const std::string prefix = argv[1];
    const std::string metric_prefix = argv[2];
    const auto source_points = read_csv(argv[3], static_cast<unsigned>(std::stoul(argv[4])));

    auto first_out = RoutingKit::load_vector<unsigned>(path(prefix, ".first_out.u32"));
    auto head = RoutingKit::load_vector<unsigned>(path(prefix, ".head.u32"));
    auto distance = RoutingKit::load_vector<unsigned>(path(prefix, ".distance.u32"));
    auto latitude = RoutingKit::load_vector<float>(path(prefix, ".latitude.f32"));
    auto longitude = RoutingKit::load_vector<float>(path(prefix, ".longitude.f32"));
    auto bicycle_weight = RoutingKit::load_vector<unsigned>(path(metric_prefix, ".bicycle.input_weight.u32"));
    auto first_geometry = RoutingKit::load_vector<unsigned>(path(prefix, ".first_geometry.u32"));
    auto geometry_latitude = RoutingKit::load_vector<float>(path(prefix, ".geometry_latitude.f32"));
    auto geometry_longitude = RoutingKit::load_vector<float>(path(prefix, ".geometry_longitude.f32"));
    auto arc_geometry_id = RoutingKit::load_vector<unsigned>(path(prefix, ".arc_geometry_id.u32"));
    auto arc_geometry_reversed = RoutingKit::load_vector<unsigned char>(path(prefix, ".arc_geometry_reversed.u8"));
    if (first_out.size() != latitude.size() + 1 || first_out.back() != head.size()
        || head.size() != distance.size() || head.size() != bicycle_weight.size() || head.size() != arc_geometry_id.size()
        || head.size() != arc_geometry_reversed.size() || geometry_latitude.size() != geometry_longitude.size()
        || first_geometry.empty() || first_geometry.back() != geometry_latitude.size()) {
        throw std::runtime_error("inconsistent graph geometry arrays");
    }

    std::unordered_map<std::uint64_t, std::vector<unsigned>> grid;
    grid.reserve(latitude.size() / 12);
    for (unsigned node = 0; node < latitude.size(); ++node) {
        grid[grid_key(cell(latitude[node]), cell(longitude[node]))].push_back(node);
    }
    const auto nearest_node = [&](const Coordinate& point) {
        unsigned selected = RoutingKit::invalid_id;
        double best = std::numeric_limits<double>::infinity();
        const int row = cell(point.latitude);
        const int column = cell(point.longitude);
        for (int radius = 0; radius <= 3 && selected == RoutingKit::invalid_id; ++radius) {
            for (int y = row - radius; y <= row + radius; ++y) for (int x = column - radius; x <= column + radius; ++x) {
                const auto found = grid.find(grid_key(y, x));
                if (found == grid.end()) continue;
                for (unsigned node : found->second) {
                    const double candidate = distance_meters(point, {latitude[node], longitude[node]});
                    if (candidate < best) { best = candidate; selected = node; }
                }
            }
        }
        return std::pair<unsigned, double>{selected, best};
    };

    struct Anchor { std::size_t source_index; unsigned node; };
    std::vector<Anchor> anchors;
    anchors.reserve(source_points.size());
    double max_snap = 0.0;
    unsigned skipped_off_graph_points = 0;
    for (std::size_t index = 0; index < source_points.size(); ++index) {
        const auto& point = source_points[index];
        const auto [node, snap] = nearest_node(point);
        // The government source is an overview trace.  Some coordinates are
        // deliberately offset far from the bicycle graph; using them as route
        // anchors creates the exact cross-country chords this tool exists to
        // prevent.  Keep only coordinates that can be tied to a real road.
        if (node == RoutingKit::invalid_id || snap > 600.0) {
            ++skipped_off_graph_points;
            continue;
        }
        anchors.push_back({index, node});
        max_snap = std::max(max_snap, snap);
    }
    if (anchors.size() < 2) throw std::runtime_error("official route has fewer than two graph anchors");

    auto tail = RoutingKit::invert_inverse_vector(first_out);
    auto order = RoutingKit::compute_nested_node_dissection_order_using_inertial_flow(
        static_cast<unsigned>(latitude.size()), tail, head, latitude, longitude);
    RoutingKit::CustomizableContractionHierarchy cch(order, tail, head);
    RoutingKit::CustomizableContractionHierarchyMetric bicycle(cch, bicycle_weight);
    std::vector<unsigned> shortest_weight(distance.size());
    for (std::size_t arc = 0; arc < distance.size(); ++arc) {
        shortest_weight[arc] = std::max(1u, distance[arc] * 10u);
    }
    RoutingKit::CustomizableContractionHierarchyMetric shortest(cch, shortest_weight);
    bicycle.customize();
    shortest.customize();
    RoutingKit::CustomizableContractionHierarchyQuery query(bicycle);

    std::vector<std::vector<Coordinate>> restored_segments(1);
    restored_segments.back().reserve(source_points.size() * 3);
    auto append = [&](const Coordinate& point) {
        auto& restored = restored_segments.back();
        if (restored.empty() || distance_meters(restored.back(), point) > 0.05) restored.push_back(point);
    };
    unsigned nontrivial_legs = 0;
    unsigned corridor_detours = 0;
    unsigned shortest_fallback_legs = 0;
    double restored_distance = 0.0;
    for (std::size_t index = 0; index + 1 < anchors.size(); ++index) {
        const unsigned source = anchors[index].node;
        const unsigned target = anchors[index + 1].node;
        if (source == target) continue;
        query.reset(bicycle).add_source(source).add_target(target).run();
        if (query.get_distance() == RoutingKit::inf_weight) {
            query.reset(shortest).add_source(source).add_target(target).run();
            if (query.get_distance() == RoutingKit::inf_weight) {
                // Start a separate visible polyline after the missing graph
                // connection.  Rendering must never bridge it with a straight
                // line across terrain.
                std::cerr << "unconnectedSourceLeg=" << anchors[index].source_index
                          << "->" << anchors[index + 1].source_index << '\n';
                if (restored_segments.back().size() > 1) restored_segments.emplace_back();
                continue;
            }
            ++shortest_fallback_legs;
        }
        const auto arcs = query.get_arc_path();
        double leg_distance = 0.0;
        for (unsigned arc : arcs) {
            leg_distance += distance_meters({latitude[tail[arc]], longitude[tail[arc]]},
                                            {latitude[head[arc]], longitude[head[arc]]});
            const unsigned geometry = arc_geometry_id[arc];
            const unsigned begin = first_geometry[geometry];
            const unsigned end = first_geometry[geometry + 1];
            if (arc_geometry_reversed[arc]) {
                for (unsigned point = end; point-- > begin;) append({geometry_latitude[point], geometry_longitude[point]});
            } else {
                for (unsigned point = begin; point < end; ++point) append({geometry_latitude[point], geometry_longitude[point]});
            }
        }
        const double direct = distance_meters(source_points[anchors[index].source_index],
                                              source_points[anchors[index + 1].source_index]);
        if (direct < 500.0 && leg_distance > std::max(850.0, direct * 5.0)) {
            // Preserve the actual graph geometry even when a coarse government
            // sample lands on the other side of a divided road.  A detour is
            // diagnosable; a fabricated straight chord is never acceptable.
            ++corridor_detours;
        }
        restored_distance += leg_distance;
        ++nontrivial_legs;
    }
    restored_segments.erase(std::remove_if(restored_segments.begin(), restored_segments.end(),
        [](const auto& segment) { return segment.size() < 2; }), restored_segments.end());
    std::size_t restored_point_count = 0;
    for (const auto& segment : restored_segments) restored_point_count += segment.size();
    if (restored_point_count < 2) throw std::runtime_error("restored route has no geometry");
    write_json(argv[5], restored_segments);
    std::cerr << "restoredPoints=" << restored_point_count << " restoredSegments=" << restored_segments.size()
              << " routedLegs=" << nontrivial_legs
              << " sourcePoints=" << source_points.size() << " graphAnchors=" << anchors.size()
              << " skippedOffGraphPoints=" << skipped_off_graph_points << " maxSnapMeters=" << max_snap
              << " corridorDetours=" << corridor_detours << " shortestFallbackLegs=" << shortest_fallback_legs
              << " restoredArcDistanceMeters=" << restored_distance << '\n';
    return 0;
} catch (const std::exception& error) {
    std::cerr << "restore official route geometry failed: " << error.what() << '\n';
    return 1;
}
