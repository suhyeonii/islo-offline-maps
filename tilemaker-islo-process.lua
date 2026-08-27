-- Keep the upstream OpenMapTiles-compatible rendering behavior, then attach
-- the immutable OSM identity to every named feature written by that process.
-- WritePOI and the named-feature catch-all both call SetNameAttributes, so POI
-- Points retain their exact node/way/relation identity without changing their
-- geometry, rank, class, or collision behavior.
dofile("/usr/src/app/resources/process-openmaptiles.lua")

local upstreamSetNameAttributes = SetNameAttributes

function SetNameAttributes()
	upstreamSetNameAttributes()
	Attribute("osm_type", OsmType())
	Attribute("osm_id", Id())
end
