-- Keep the upstream OpenMapTiles-compatible rendering behavior, then attach
-- the immutable OSM identity to every named feature written by that process.
-- WritePOI and the named-feature catch-all both call SetNameAttributes, so POI
-- Points retain their exact node/way/relation identity without changing their
-- geometry, rank, class, or collision behavior.
dofile("/usr/src/app/resources/process-openmaptiles.lua")

local upstreamSetNameAttributes = SetNameAttributes
local upstreamWayFunction = way_function

function SetNameAttributes()
	upstreamSetNameAttributes()
	Attribute("osm_type", OsmType())
	Attribute("osm_id", Id())
end

-- The upstream OpenMapTiles process expects a separate ocean shapefile for
-- coastlines. Regional Islo builds use only the OSM PBF, so natural=coastline
-- ways otherwise disappear completely. Preserve them as an explicit line
-- layer; this also avoids relying on clipped water polygons at region edges.
function way_function()
	if Find("natural") == "coastline" then
		Layer("coastline", false)
		MinZoom(0)
		Attribute("class", "coastline")
	end
	upstreamWayFunction()
end
