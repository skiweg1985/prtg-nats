"""Which version is where, and what the interface is allowed to conclude.

The three versions - running, checkout, remote - are easy to collapse into one
"is there an update" boolean, and every way of doing that is wrong in a way
that shows an operator something untrue. These tests pin the two cases that
matter: a repository that cannot be reached must never read as up to date, and
an image built without a version stamp must not be compared to anything.
"""

from __future__ import annotations

from app.services.stack_update import StackUpdateService, _parse_probe

state = StackUpdateService.state


def test_up_to_date_when_all_three_agree() -> None:
    assert (
        state(running="abc", checkout="abc", remote="abc", reachable=True) == "current"
    )


def test_a_newer_branch_is_an_available_update() -> None:
    assert (
        state(running="abc", checkout="abc", remote="def", reachable=True)
        == "update_available"
    )


def test_a_pulled_but_unbuilt_checkout_is_its_own_state() -> None:
    """`git pull` without a rebuild. The stack still runs the old code.

    Reporting this as "up to date" because the checkout matches the branch is
    the failure this state exists to prevent - the operator would be told they
    are current while running something else entirely.
    """
    assert (
        state(running="abc", checkout="def", remote="def", reachable=True)
        == "rebuild_pending"
    )


def test_an_unreachable_repository_never_reads_as_up_to_date() -> None:
    """A deploy key that stopped working must not look like good news.

    Without this an installation would sit on an old version and report itself
    current for as long as nobody looked at the log.
    """
    assert (
        state(running="abc", checkout="abc", remote="", reachable=False)
        == "unreachable"
    )


def test_an_unstamped_image_admits_it_does_not_know() -> None:
    """Built without GIT_COMMIT - by hand, or by an older compose file.

    There is nothing to compare, so the honest answer is that this is unknown.
    Guessing from the checkout would state something about the running code
    that nobody verified.
    """
    assert state(running="", checkout="abc", remote="abc", reachable=True) == "unknown"


def test_the_probe_object_is_found_among_the_warnings() -> None:
    """stdout and stderr arrive interleaved through the container log.

    The updater warns about an unpinned host key on stderr, and that warning
    lands in the same stream as the JSON. Parsing the whole text would fail on
    exactly the installations that have not pinned a key yet.
    """
    output = (
        "updater: no pinned host key at /srv/.../git_known_hosts; "
        "the connection is unauthenticated\n"
        '{"branch":"dev","head":"aaa","dirty":false,"remote_head":"bbb",'
        '"behind":2,"ahead":0,"reachable":true,"error":"","commits":[]}\n'
    )
    result = _parse_probe(output, fallback_branch="main")
    assert result.branch == "dev"
    assert result.head == "aaa"
    assert result.remote_head == "bbb"
    assert result.reachable is True


def test_output_without_any_object_is_a_failure_not_an_empty_reading() -> None:
    """A container that died before writing anything.

    An empty ProbeResult with reachable=True would be indistinguishable from a
    healthy installation with no updates, so the parse insists on the opposite.
    """
    result = _parse_probe(
        "updater: /srv/checkout is not a git checkout\n", fallback_branch="main"
    )
    assert result.reachable is False
    assert "not a git checkout" in result.error
