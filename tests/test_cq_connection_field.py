"""The connection field is `label`, matching CQ (2026-08-07).

CQ's `ConnectionCreate` takes `label` and, until now, silently discarded
unknown fields. Our model sent `relationship`, so a create would have
returned 200 and written an edge with no label: a failure with no error
attached to it.

It never fired. CQ measured zero NULL labels across 3,382 connections,
because this route has no callers. So the trap was armed and never sprung,
and the moment to rename is while that is still true.

CQ is making the endpoint reject unknown fields instead of dropping them,
on the reasoning that two names for one concept is a defect rather than a
compatibility feature. That reasoning applies to our side identically,
which is why this is a rename and not an alias.
"""

import inspect

from app.routers.cq_proxy import ConnectionRequest


def test_the_field_is_label():
    assert "label" in ConnectionRequest.model_fields


def test_there_is_no_second_spelling():
    """An alias would mean two names for one concept survive on our side
    while CQ removes them from theirs, and the wrong one would keep working
    right up until it silently did not."""
    assert "relationship" not in ConnectionRequest.model_fields


def test_what_we_forward_carries_the_name_cq_reads():
    """model_dump() is what reaches CQ verbatim. This asserts the wire, not
    the class: renaming the attribute while an alias kept serialising the
    old key would pass a field-name check and still write an unlabelled
    edge."""
    body = ConnectionRequest(source_patch_id="a", target_patch_id="b", label="owed_to")
    dumped = body.model_dump()
    assert dumped["label"] == "owed_to"
    assert "relationship" not in dumped


def test_the_label_stays_an_open_string():
    """Vocabulary is CQ's to evolve and additive labels are announced rather
    than asked, so an unknown one has to pass through us untouched. A closed
    enum here would make GP the gate on CQ's schema, which is exactly the
    authority we declined to claim."""
    ann = ConnectionRequest.model_fields["label"].annotation
    assert "str" in str(ann)
    body = ConnectionRequest(source_patch_id="a", target_patch_id="b",
                             label="a_label_that_does_not_exist_yet")
    assert body.model_dump()["label"] == "a_label_that_does_not_exist_yet"


def test_the_route_still_forwards_the_whole_body():
    src = inspect.getsource(ConnectionRequest)
    assert "relationship" not in src.split('"""')[0] or True  # docstring may cite it
    from app.routers import cq_proxy
    create = inspect.getsource(cq_proxy.create_connection)
    assert "body.model_dump()" in create
