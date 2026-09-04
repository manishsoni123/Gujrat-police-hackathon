"""Permission matrix and department scoping (CONTRACT §2.3, §2.4)."""

from __future__ import annotations

import pytest

from app.core.rbac import PERMISSIONS, ROLES, Scope, can_change_department, has_permission, permissions_for, scope_for

# The exact matrix of CONTRACT §2.4: permission → roles that hold it.
MATRIX = {
    "cameras.read": {"admin", "dept_admin", "operator", "viewer"},
    "cameras.write": {"admin", "dept_admin"},
    "cameras.export": {"admin", "dept_admin", "operator"},
    "analytics.read": {"admin", "dept_admin", "operator", "viewer"},
    "watchlist.write": {"admin", "dept_admin", "operator"},
    "alerts.ack": {"admin", "dept_admin", "operator"},
    "route.confirm": {"admin", "dept_admin", "operator"},
    "events.write": {"admin", "dept_admin", "operator"},
    "reports.export": {"admin", "dept_admin", "operator"},
    "external.lookup": {"admin", "dept_admin", "operator"},
    "zones.write": {"admin", "dept_admin"},
    "admin.users": {"admin"},
    "admin.audit": {"admin"},
    "admin.apikeys": {"admin"},
    "admin.settings": {"admin"},
    "settings.read_public": {"admin", "dept_admin", "operator", "viewer"},
}


def test_matrix_is_exactly_the_contract() -> None:
    assert set(PERMISSIONS) == set(MATRIX)
    for perm, roles in MATRIX.items():
        assert set(PERMISSIONS[perm]) == roles, perm


@pytest.mark.parametrize("role", ROLES)
def test_permissions_for_role(role: str) -> None:
    expected = {p for p, roles in MATRIX.items() if role in roles}
    assert set(permissions_for(role)) == expected
    for p in MATRIX:
        assert has_permission(role, p) == (role in MATRIX[p])


def test_unknown_role_or_permission() -> None:
    assert has_permission("root", "cameras.read") is False
    assert has_permission("admin", "does.not.exist") is False
    assert permissions_for("nobody") == []


def test_viewer_cannot_mutate() -> None:
    for p in ("cameras.write", "watchlist.write", "alerts.ack", "route.confirm", "events.write", "reports.export", "cameras.export"):
        assert not has_permission("viewer", p)


def test_operator_cannot_edit_cameras_but_can_run_analytics_actions() -> None:
    assert not has_permission("operator", "cameras.write")
    assert has_permission("operator", "watchlist.write")
    assert has_permission("operator", "alerts.ack")
    assert has_permission("operator", "reports.export")
    assert not has_permission("operator", "admin.audit")


def test_scope_only_for_dept_admin() -> None:
    assert scope_for("admin", 2, "Gandhinagar").unrestricted
    assert scope_for("operator", 2, None).unrestricted
    assert scope_for("viewer", 2, "Surat").unrestricted
    s = scope_for("dept_admin", 2, None)
    assert not s.unrestricted
    assert s.department_id == 2 and s.district is None
    # a dept_admin without a department is (defensively) unrestricted
    assert scope_for("dept_admin", None, None).unrestricted


def test_scope_allows_department_and_district() -> None:
    dept_only = Scope(department_id=2)
    assert dept_only.allows(2, "Gandhinagar")
    assert dept_only.allows(2, None)
    assert not dept_only.allows(3, "Gandhinagar")
    assert not dept_only.allows(None, "Gandhinagar")
    with_district = Scope(department_id=2, district="Gandhinagar")
    assert with_district.allows(2, "Gandhinagar")
    assert not with_district.allows(2, "Ahmedabad")
    assert not with_district.allows(2, None)
    assert with_district.as_dict() == {"department_id": 2, "district": "Gandhinagar"}
    assert Scope().allows(99, "Anywhere")
    assert Scope().as_dict() == {"department_id": None, "district": None}


def test_only_admin_moves_cameras_between_departments() -> None:
    assert can_change_department("admin")
    for role in ("dept_admin", "operator", "viewer"):
        assert not can_change_department(role)


def test_scope_sql_conditions_shape() -> None:
    from app.services.scope import camera_conditions

    assert camera_conditions(Scope()) == []
    assert len(camera_conditions(Scope(department_id=2))) == 1
    assert len(camera_conditions(Scope(department_id=2, district="Surat"))) == 2


# ---- media (MediaMTX) authorisation behind Caddy forward_auth: services/media_auth.py

from app.services.media_auth import camera_id_from_path, media_path_from_uri, token_from_uri  # noqa: E402


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("/mtx/cam_2/index.m3u8", "cam_2"),
        ("/mtx/cam_2/index.m3u8?cookieCheck=1", "cam_2"),
        ("/mtx/cam_2/seg_0012.mp4", "cam_2"),
        ("/mtx/cam_8_h264/init.mp4", "cam_8_h264"),
        ("/mtx/cam_2/whep", "cam_2"),
        ("/mtx/cam_2/whep/4f1c9a", "cam_2"),
        ("/cam_2/index.m3u8", "cam_2"),  # prefix already stripped by Caddy handle_path
        ("/cam_2/whep", "cam_2"),
        ("/mtx/stream/3/index.m3u8", "stream/3"),
        ("/mtx/own_gate/index.m3u8", "own_gate"),
        ("/playback/list?path=cam_2&start=2026-09-04T10:00:00Z&end=2026-09-04T11:00:00Z", "cam_2"),
        ("/playback/get?path=cam_8_h264&start=2026-09-04T10:00:00Z&duration=30&format=mp4", "cam_8_h264"),
        ("/list?path=cam_2&start=x", "cam_2"),
        ("/get?path=/cam_2/", "cam_2"),
        ("/playback/list", None),
        ("/mtx/", None),
        ("", None),
    ],
)
def test_media_path_from_uri(uri: str, expected: str | None) -> None:
    assert media_path_from_uri(uri) == expected


@pytest.mark.parametrize(
    "path,expected",
    [("cam_2", 2), ("cam_8_h264", 8), ("cam_123456", 123456), ("own_gate", None), ("stream/3", None), ("cam_", None), ("cam_2x", None), ("", None), (None, None)],
)
def test_camera_id_from_path(path: str | None, expected: int | None) -> None:
    assert camera_id_from_path(path) == expected


def test_token_from_forwarded_uri() -> None:
    assert token_from_uri("/mtx/cam_2/index.m3u8?token=abc.def.ghi") == "abc.def.ghi"
    assert token_from_uri("/playback/get?path=cam_2&token=t1&format=mp4") == "t1"
    assert token_from_uri("/mtx/cam_2/index.m3u8") is None
    assert token_from_uri("") is None


async def test_camera_allowed_scopes_dept_admin_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import media_auth

    calls: list[int] = []

    async def fake_scoped_camera(db, scope, camera_id, include_retired=True):  # noqa: ANN001
        calls.append(camera_id)
        return object() if camera_id == 2 else None

    monkeypatch.setattr(media_auth, "scoped_camera", fake_scoped_camera)
    media_auth.forget_decisions()
    police = Scope(department_id=2)
    # statewide roles pass any path without a lookup
    assert await media_auth.camera_allowed(None, 1, Scope(), "/mtx/own_gate/index.m3u8") is True
    assert await media_auth.camera_allowed(None, 1, Scope(), "/mtx/cam_999/whep") is True
    assert calls == []
    # dept_admin: in-scope camera yes, out-of-scope no, non-camera paths never
    assert await media_auth.camera_allowed(None, 4, police, "/mtx/cam_2/index.m3u8") is True
    assert await media_auth.camera_allowed(None, 4, police, "/playback/list?path=cam_3&start=a&end=b") is False
    assert await media_auth.camera_allowed(None, 4, police, "/mtx/own_gate/index.m3u8") is False
    assert await media_auth.camera_allowed(None, 4, police, "/mtx/stream/3/index.m3u8") is False
    assert await media_auth.camera_allowed(None, 4, police, "") is False
    assert calls == [2, 3]
    # repeated segment requests hit the per-(user, camera) cache
    assert await media_auth.camera_allowed(None, 4, police, "/mtx/cam_2/seg_1.mp4") is True
    assert await media_auth.camera_allowed(None, 4, police, "/mtx/cam_2/seg_2.mp4") is True
    assert calls == [2, 3]
    media_auth.forget_decisions()
