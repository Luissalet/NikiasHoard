"""Part correspondence on an articulated reference, without changing existing fixtures."""

import numpy as np
import pytest

from nikias_hoard.engine import pose, regions, source
from nikias_hoard.engine.mesh import Mesh

from . import shapes


@pytest.fixture(scope="module")
def articulated():
    mesh = shapes.figure(160, 96)
    labels = regions.segment(mesh)
    scene = shapes.textured(100, 64)
    angle = np.radians(40)
    rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0],
                         [-np.sin(angle), 0, np.cos(angle)]])
    head = scene.P[:, 2] >= 38.6
    pivot = np.array([0, 0, 38.6])
    scene.P[head] = (scene.P[head] - pivot) @ rotation.T + pivot
    points, rgb = source.sample_scene(scene, 70_000)
    return mesh, labels, points, rgb


def test_transfers_each_ball_after_the_head_bends_40_degrees(articulated):
    mesh, labels, points, rgb = articulated
    result = pose.transfer(points, rgb, mesh, labels)
    assert set(result) == set(np.unique(labels))
    truth = shapes.ball_of(mesh)
    for r, row in result.items():
        ball = np.bincount(truth[labels == r], weights=mesh.areas[labels == r]).argmax()
        assert row["rgb"] == shapes.COLORS[ball].tolist(), row
        assert row["confidence"] > 0.8, row
        assert row["part"] is not None and 0 < row["part"]["share"] < 1
    assert len({row["part"]["id"] for row in result.values()}) == 3


def test_rejects_an_unrelated_shape(articulated):
    mesh, labels, _, _ = articulated
    points = np.random.default_rng(1).uniform(-1, 1, (20_000, 3)) * [30, 4, 4]
    rgb = np.tile(shapes.COLORS[0], (len(points), 1))
    result = pose.transfer(points, rgb, mesh, labels)
    assert all(row["rgb"] is None and row["confidence"] < 0.4 and row["part"] is None
               for row in result.values())


@pytest.mark.parametrize("scale", [0.0001, 1000.0])
def test_units_and_global_orientation_do_not_change_the_matches(articulated, scale):
    mesh, labels, points, rgb = articulated
    rotation = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]])
    moved = points @ rotation.T * scale + np.array([3, -2, 8]) * scale
    target = Mesh((mesh.P + [-5, 6, 4]) / scale, mesh.F)
    before = (moved.copy(), rgb.copy(), target.P.copy(), labels.copy())
    result = pose.transfer(moved, rgb, target, labels + 10)
    truth = shapes.ball_of(mesh)
    assert set(result) == {10, 11, 12}
    for r, row in result.items():
        ball = np.bincount(truth[labels == r - 10], weights=mesh.areas[labels == r - 10]).argmax()
        assert row["rgb"] == shapes.COLORS[ball].tolist(), row
        assert row["confidence"] > 0.8, row
    for old, new in zip(before, (moved, rgb, target.P, labels)):
        np.testing.assert_array_equal(old, new)


def test_missing_head_stays_uncoloured(articulated):
    mesh, labels, points, rgb = articulated
    keep = (rgb != shapes.COLORS[2]).any(axis=1)
    result = pose.transfer(points[keep], rgb[keep], mesh, labels)
    assert result[0]["rgb"] == shapes.COLORS[0].tolist()
    assert result[1]["rgb"] == shapes.COLORS[1].tolist()
    assert result[2]["rgb"] is None and result[2]["part"] is None


def test_indistinguishable_parts_are_ambiguous():
    original = shapes.figure(80, 48)
    faces = original.F[shapes.ball_of(original) == 0]
    used, remap = np.unique(faces, return_inverse=True)
    ball = original.P[used]
    vertices = np.vstack([ball, ball + [40, 0, 0]])
    triangles = np.vstack([remap.reshape(-1, 3), remap.reshape(-1, 3) + len(ball)])
    mesh = Mesh(vertices, triangles)
    labels = np.repeat([0, 1], len(faces))
    points = mesh.centroids.copy()
    rgb = shapes.COLORS[labels]
    result = pose.transfer(points, rgb, mesh, labels)
    assert all(row["rgb"] is None and row["part"] is None for row in result.values())
    # Even a single source part cannot choose between two identical destinations.
    result = pose.transfer(points[:len(faces)], rgb[:len(faces)], mesh, labels)
    assert all(row["rgb"] is None and row["part"] is None for row in result.values())


def test_geometry_can_split_a_material_shared_by_parts(articulated):
    mesh, labels, points, rgb = articulated
    rgb = np.tile(shapes.COLORS[0], (len(rgb), 1))
    result = pose.transfer(points, rgb, mesh, labels)
    assert all(row["rgb"] == shapes.COLORS[0].tolist() and row["confidence"] > 0.6
               for row in result.values()), result


def test_dominant_colour_survives_texture_variation(articulated):
    mesh, labels, points, rgb = articulated
    varied = rgb.copy()
    varied[::5] += 10
    result = pose.transfer(points, varied, mesh, labels)
    assert [result[r]["rgb"] for r in sorted(result)] == shapes.COLORS.tolist()
    assert all(row["confidence"] > 0.8 for row in result.values())


def test_empty_and_degenerate_sources_abstain(articulated):
    mesh, labels, _, _ = articulated
    for points in (np.empty((0, 3)), np.ones((100, 3))):
        result = pose.transfer(points, np.zeros_like(points), mesh, labels)
        assert all(row["rgb"] is None and row["confidence"] == 0 for row in result.values())


@pytest.mark.parametrize("bad", ["colours", "nan", "regions"])
def test_validates_inputs(articulated, bad):
    mesh, labels, points, rgb = articulated
    points, rgb, labels = points.copy(), rgb.copy(), labels.copy()
    if bad == "colours":
        rgb = rgb[:-1]
    elif bad == "nan":
        points[0, 0] = np.nan
    else:
        labels[0] = -1
    with pytest.raises(ValueError):
        pose.transfer(points, rgb, mesh, labels)
