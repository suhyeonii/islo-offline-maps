CXX ?= clang++
CXXFLAGS ?= -O3 -DNDEBUG -std=c++17
ROUTINGKIT_DIR := ../vendor/RoutingKit

.PHONY: all clean smoke

all: build_cch_prototype extract_bicycle_graph benchmark_cch_arrays build_geometry_index audit_components

build_cch_prototype: build_cch_prototype.cpp $(ROUTINGKIT_DIR)/lib/libroutingkit.a
	$(CXX) $(CXXFLAGS) -I$(ROUTINGKIT_DIR)/include $< \
		$(ROUTINGKIT_DIR)/lib/libroutingkit.a -lsqlite3 -o $@

extract_bicycle_graph: extract_bicycle_graph.cpp $(ROUTINGKIT_DIR)/lib/libroutingkit.a
	$(CXX) $(CXXFLAGS) -I$(ROUTINGKIT_DIR)/include $< \
		$(ROUTINGKIT_DIR)/lib/libroutingkit.a -lz -pthread -o $@

benchmark_cch_arrays: benchmark_cch_arrays.cpp $(ROUTINGKIT_DIR)/lib/libroutingkit.a
	$(CXX) $(CXXFLAGS) -I$(ROUTINGKIT_DIR)/include $< \
		$(ROUTINGKIT_DIR)/lib/libroutingkit.a -lz -pthread -o $@

build_geometry_index: build_geometry_index.cpp $(ROUTINGKIT_DIR)/lib/libroutingkit.a
	$(CXX) $(CXXFLAGS) -I$(ROUTINGKIT_DIR)/include $< \
		$(ROUTINGKIT_DIR)/lib/libroutingkit.a -o $@

audit_components: audit_components.cpp $(ROUTINGKIT_DIR)/lib/libroutingkit.a
	$(CXX) $(CXXFLAGS) -I$(ROUTINGKIT_DIR)/include $< \
		$(ROUTINGKIT_DIR)/lib/libroutingkit.a -o $@

smoke: build_cch_prototype
	mkdir -p ../build/cch-audit
	./build_cch_prototype ../build/korea.routing.v24.sqlite 100000 > ../build/cch-audit/smoke-report.json
	python3 validate_report.py ../build/cch-audit/smoke-report.json

clean:
	rm -f build_cch_prototype extract_bicycle_graph benchmark_cch_arrays build_geometry_index audit_components
