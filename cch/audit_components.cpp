#include <routingkit/inverse_vector.h>
#include <routingkit/vector_io.h>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <limits>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
std::string path(const std::string& prefix, const char* suffix) { return prefix + suffix; }

struct DSU {
    std::vector<unsigned> parent, size;
    explicit DSU(unsigned count) : parent(count), size(count, 1) {
        for (unsigned i = 0; i < count; ++i) parent[i] = i;
    }
    unsigned root(unsigned x) {
        unsigned r = x;
        while (parent[r] != r) r = parent[r];
        while (parent[x] != x) { const unsigned n = parent[x]; parent[x] = r; x = n; }
        return r;
    }
    void join(unsigned a, unsigned b) {
        a = root(a); b = root(b);
        if (a == b) return;
        if (size[a] < size[b]) std::swap(a, b);
        parent[b] = a; size[a] += size[b];
    }
};

struct Component {
    unsigned root = 0;
    unsigned nodes = 0;
    float south = std::numeric_limits<float>::max();
    float west = std::numeric_limits<float>::max();
    float north = -std::numeric_limits<float>::max();
    float east = -std::numeric_limits<float>::max();
};

const char* geography(const Component& c) {
    const float center_lat = (c.south + c.north) * 0.5f;
    const float center_lon = (c.west + c.east) * 0.5f;
    if (center_lat >= 33.0f && center_lat <= 34.2f && center_lon >= 126.0f && center_lon <= 127.2f)
        return "jeju";
    if (center_lat >= 37.0f && center_lon >= 130.5f) return "ulleung_dokdo";
    if (c.nodes <= 10) return "tiny";
    return "review";
}
}

int main(int argc, char** argv) try {
    if (argc != 2) {
        std::cerr << "usage: " << argv[0] << " graph-prefix\n";
        return 2;
    }
    const std::string prefix = argv[1];
    auto first_out = RoutingKit::load_vector<unsigned>(path(prefix, ".first_out.u32"));
    auto head = RoutingKit::load_vector<unsigned>(path(prefix, ".head.u32"));
    auto latitude = RoutingKit::load_vector<float>(path(prefix, ".latitude.f32"));
    auto longitude = RoutingKit::load_vector<float>(path(prefix, ".longitude.f32"));
    if (first_out.size() != latitude.size() + 1 || longitude.size() != latitude.size()
        || first_out.back() != head.size()) throw std::runtime_error("incompatible graph arrays");
    const auto tail = RoutingKit::invert_inverse_vector(first_out);
    DSU dsu(static_cast<unsigned>(latitude.size()));
    for (std::size_t arc = 0; arc < head.size(); ++arc) dsu.join(tail[arc], head[arc]);
    std::unordered_map<unsigned, Component> by_root;
    by_root.reserve(latitude.size() / 8);
    std::vector<unsigned> node_component(latitude.size());
    for (unsigned node = 0; node < latitude.size(); ++node) {
        const unsigned root = dsu.root(node);
        node_component[node] = root;
        auto& c = by_root[root];
        c.root = root; ++c.nodes;
        c.south = std::min(c.south, latitude[node]); c.north = std::max(c.north, latitude[node]);
        c.west = std::min(c.west, longitude[node]); c.east = std::max(c.east, longitude[node]);
    }
    std::vector<Component> components;
    components.reserve(by_root.size());
    for (const auto& item : by_root) components.push_back(item.second);
    std::sort(components.begin(), components.end(), [](const auto& a, const auto& b) {
        return a.nodes > b.nodes;
    });
    RoutingKit::save_vector(path(prefix, ".component.u32"), node_component);
    std::uint64_t tiny_nodes = 0, jeju_nodes = 0, review_nodes = 0;
    for (std::size_t i = 1; i < components.size(); ++i) {
        const std::string kind = geography(components[i]);
        if (kind == "tiny") tiny_nodes += components[i].nodes;
        else if (kind == "jeju") jeju_nodes += components[i].nodes;
        else review_nodes += components[i].nodes;
    }
    std::cout << "{\n  \"nodeCount\": " << latitude.size()
              << ",\n  \"componentCount\": " << components.size()
              << ",\n  \"largestNodes\": " << components.front().nodes
              << ",\n  \"largestRatio\": " << static_cast<double>(components.front().nodes) / latitude.size()
              << ",\n  \"jejuNodesOutsideLargest\": " << jeju_nodes
              << ",\n  \"tinyNodesOutsideLargest\": " << tiny_nodes
              << ",\n  \"reviewNodesOutsideLargest\": " << review_nodes
              << ",\n  \"largestOutsideComponents\": [\n";
    const std::size_t limit = std::min<std::size_t>(50, components.size() - 1);
    for (std::size_t i = 1; i <= limit; ++i) {
        const auto& c = components[i];
        std::cout << "    {\"nodes\":" << c.nodes << ",\"kind\":\"" << geography(c)
                  << "\",\"south\":" << c.south << ",\"west\":" << c.west
                  << ",\"north\":" << c.north << ",\"east\":" << c.east << "}"
                  << (i == limit ? "\n" : ",\n");
    }
    std::cout << "  ]\n}\n";
    return 0;
} catch (const std::exception& error) {
    std::cerr << "component audit failed: " << error.what() << '\n';
    return 1;
}
