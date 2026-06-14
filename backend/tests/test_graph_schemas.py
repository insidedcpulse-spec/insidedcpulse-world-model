from datetime import datetime, timezone

from app.schemas import (
    CausalChainEntry,
    CausalChainResponse,
    GraphEdge,
    GraphNode,
    GraphNodeDetail,
    NeighborEntry,
    NeighborsResponse,
    PathResponse,
    RelatedEntitiesResponse,
    RelatedEntity,
    TimelineEntry,
    TimelineResponse,
)


def test_graph_schema_models_construct():
    now = datetime.now(timezone.utc)
    node = GraphNode(id="service.checkout", type="service", label="service.checkout",
                      metadata={}, created_at=now, updated_at=now)
    edge = GraphEdge(source_node="incident.inc3", target_node="service.checkout", edge_type="REFERENCES",
                      weight=1.0, metadata={}, source_event_id=42, created_at=now)

    detail = GraphNodeDetail(node=node, edges_out=[edge], edges_in=[])
    assert detail.node.id == "service.checkout"
    assert detail.edges_out[0].edge_type == "REFERENCES"

    neighbor = NeighborEntry(node=node, edge=edge, direction="out")
    neighbors = NeighborsResponse(node_id="incident.inc3", edge_type=None, direction="both", neighbors=[neighbor])
    assert neighbors.neighbors[0].direction == "out"

    path = PathResponse(from_id="a", to_id="b", found=False, path=[], edges=[], depth=0)
    assert path.found is False

    entry = TimelineEntry(event_id="event.42", label="x", metadata={}, weight=1.0,
                           edge_metadata={}, source_event_id=42, created_at=now)
    timeline = TimelineResponse(entity=None, events=[entry])
    assert timeline.events[0].event_id == "event.42"

    causal_entry = CausalChainEntry(depth=1, source_node="alert.a1", target_node="incident.inc3",
                                     weight=0.63, metadata={"rule_id": "alert_precedes_incident"}, source_event_id=40)
    chain = CausalChainResponse(node_id="incident.inc3", direction="upstream", chain=[causal_entry])
    assert chain.chain[0].depth == 1

    related = RelatedEntity(node=node, distance=1, edge_type="OWNED_BY")
    related_resp = RelatedEntitiesResponse(node_id="service.checkout", max_depth=2, related=[related])
    assert related_resp.related[0].distance == 1
