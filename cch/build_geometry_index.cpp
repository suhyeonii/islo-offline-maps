#include <routingkit/inverse_vector.h>
#include <routingkit/vector_io.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using Clock = std::chrono::steady_clock;

namespace {
double elapsed_ms(Clock::time_point start) {
    return std::chrono::duration<double, std::milli>(Clock::now() - start).count();
}
std::string path(const std::string& prefix, const char* suffix) { return prefix + suffix; }
std::uint64_t cell_key(int latitude, int longitude) {
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(latitude)) << 32)
        | static_cast<std::uint32_t>(longitude);
}
}

int main(int argc, char** argv) try {
    if (argc != 3) {
        std::cerr << "usage: " << argv[0] << " bicycle-array-prefix index-output-prefix\n";
        return 2;
    }
    const std::string input = argv[1];
    const std::string output = argv[2];
    const auto started = Clock::now();
    auto first_out = RoutingKit::load_vector<unsigned>(path(input, ".first_out.u32"));
    auto head = RoutingKit::load_vector<unsigned>(path(input, ".head.u32"));
    auto latitude = RoutingKit::load_vector<float>(path(input, ".latitude.f32"));
    auto longitude = RoutingKit::load_vector<float>(path(input, ".longitude.f32"));
    auto first_geometry = RoutingKit::load_vector<unsigned>(path(input, ".first_geometry.u32"));
    auto geometry_latitude = RoutingKit::load_vector<float>(path(input, ".geometry_latitude.f32"));
    auto geometry_longitude = RoutingKit::load_vector<float>(path(input, ".geometry_longitude.f32"));
    auto arc_geometry = RoutingKit::load_vector<unsigned>(path(input, ".arc_geometry_id.u32"));
    if (first_out.empty() || first_out.back() != head.size() || head.size() != arc_geometry.size()
        || latitude.size() + 1 != first_out.size() || longitude.size() != latitude.size()
        || first_geometry.empty() || first_geometry.back() != geometry_latitude.size()
        || geometry_latitude.size() != geometry_longitude.size()) {
        throw std::runtime_error("geometry input arrays are inconsistent");
    }
    const auto tail = RoutingKit::invert_inverse_vector(first_out);
    const unsigned geometry_count = static_cast<unsigned>(first_geometry.size() - 1);

    std::vector<unsigned> first_arc_of_geometry(geometry_count + 1, 0);
    for (unsigned geometry : arc_geometry) {
        if (geometry >= geometry_count) throw std::runtime_error("invalid arc geometry ID");
        ++first_arc_of_geometry[geometry + 1];
    }
    for (unsigned geometry = 0; geometry < geometry_count; ++geometry) {
        first_arc_of_geometry[geometry + 1] += first_arc_of_geometry[geometry];
    }
    std::vector<unsigned> arc_of_geometry(arc_geometry.size());
    std::vector<unsigned> cursor = first_arc_of_geometry;
    for (unsigned arc = 0; arc < arc_geometry.size(); ++arc) {
        arc_of_geometry[cursor[arc_geometry[arc]]++] = arc;
    }

    constexpr double cells_per_degree = 100.0;
    std::vector<std::pair<std::uint64_t, unsigned>> cell_geometry;
    cell_geometry.reserve(geometry_count * 2);
    for (unsigned geometry = 0; geometry < geometry_count; ++geometry) {
        if (first_arc_of_geometry[geometry] == first_arc_of_geometry[geometry + 1]) continue;
        const unsigned arc = arc_of_geometry[first_arc_of_geometry[geometry]];
        float south = std::min(latitude[tail[arc]], latitude[head[arc]]);
        float north = std::max(latitude[tail[arc]], latitude[head[arc]]);
        float west = std::min(longitude[tail[arc]], longitude[head[arc]]);
        float east = std::max(longitude[tail[arc]], longitude[head[arc]]);
        for (unsigned point = first_geometry[geometry]; point < first_geometry[geometry + 1]; ++point) {
            south = std::min(south, geometry_latitude[point]);
            north = std::max(north, geometry_latitude[point]);
            west = std::min(west, geometry_longitude[point]);
            east = std::max(east, geometry_longitude[point]);
        }
        const int min_y = static_cast<int>(std::floor((south + 90.0) * cells_per_degree));
        const int max_y = static_cast<int>(std::floor((north + 90.0) * cells_per_degree));
        const int min_x = static_cast<int>(std::floor((west + 180.0) * cells_per_degree));
        const int max_x = static_cast<int>(std::floor((east + 180.0) * cells_per_degree));
        for (int y = min_y; y <= max_y; ++y) {
            for (int x = min_x; x <= max_x; ++x) cell_geometry.emplace_back(cell_key(y, x), geometry);
        }
    }
    std::sort(cell_geometry.begin(), cell_geometry.end());
    cell_geometry.erase(std::unique(cell_geometry.begin(), cell_geometry.end()), cell_geometry.end());
    std::vector<std::uint64_t> cell_keys;
    std::vector<unsigned> first_geometry_of_cell = {0};
    std::vector<unsigned> geometry_of_cell;
    for (std::size_t index = 0; index < cell_geometry.size();) {
        const std::uint64_t key = cell_geometry[index].first;
        cell_keys.push_back(key);
        do {
            geometry_of_cell.push_back(cell_geometry[index].second);
            ++index;
        } while (index < cell_geometry.size() && cell_geometry[index].first == key);
        first_geometry_of_cell.push_back(static_cast<unsigned>(geometry_of_cell.size()));
    }

    RoutingKit::save_vector(path(output, ".first_arc_of_geometry.u32"), first_arc_of_geometry);
    RoutingKit::save_vector(path(output, ".arc_of_geometry.u32"), arc_of_geometry);
    RoutingKit::save_vector(path(output, ".cell_key.u64"), cell_keys);
    RoutingKit::save_vector(path(output, ".first_geometry_of_cell.u32"), first_geometry_of_cell);
    RoutingKit::save_vector(path(output, ".geometry_of_cell.u32"), geometry_of_cell);
    std::cout << "{\n"
              << "  \"geometryCount\": " << geometry_count << ",\n"
              << "  \"cellCount\": " << cell_keys.size() << ",\n"
              << "  \"cellGeometryEntries\": " << geometry_of_cell.size() << ",\n"
              << "  \"totalMs\": " << elapsed_ms(started) << "\n"
              << "}\n";
    return 0;
} catch (const std::exception& error) {
    std::cerr << "CCH geometry index failed: " << error.what() << '\n';
    return 1;
}
