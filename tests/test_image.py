import pathlib
from typing import Any, Dict

import httpx
import pytest

from registry_client.errors import ImageNotFoundError
from registry_client.image import ImageClient, ImagePullOptions
from registry_client.platforms import OS, Arch, Platform
from registry_client.utlis import (
    DEFAULT_REGISTRY_HOST,
    DEFAULT_REPO,
    parse_normalized_named,
)
from tests.local_docker import LocalDockerChecker

DEFAULT_IMAGE_NAME = "library/hello-world"


class TestImage:
    @staticmethod
    def _check_pull_image(checker: LocalDockerChecker, image_client: ImageClient, ref, options: ImagePullOptions):
        image_path = image_client.pull(ref=ref, options=options)
        assert image_path.exists() and image_path.is_file()
        save_dir = options.save_dir
        assert image_path.parent == save_dir
        checker.check_load(image_path).check_tag(image_client.repo_tag(ref)).check_platform(options.platform)

    @pytest.mark.parametrize(
        "image_name, options",
        (
            (DEFAULT_IMAGE_NAME + ":latest", {}),
            (DEFAULT_IMAGE_NAME + ":latest", {"platform": Platform(os=OS.Linux, architecture=Arch.ARM_64)}),
            (f"{DEFAULT_IMAGE_NAME}:linux", {}),
            (f"{DEFAULT_IMAGE_NAME}@sha256:f54a58bc1aac5ea1a25d796ae155dc228b3f0e11d046ae276b39c4bf2f13d8c4", {}),
        ),
    )
    def test_pull(self, image_client, image_save_dir, image_name, options: Dict[str, Any], image_checker):
        options.update(save_dir=image_save_dir)
        pull_options = ImagePullOptions(**options)
        ref = parse_normalized_named(image_name)
        self._check_pull_image(image_checker, image_client, ref, pull_options)

    @pytest.mark.parametrize(
        "target", [":error-tag", "@sha256:1111111111111111111111111111111111111111111111111111111111111111"]
    )
    def test_pull_dont_exists_ref(self, image_client, target):
        ref = parse_normalized_named(f"hello-world{target}")
        with pytest.raises(ImageNotFoundError):
            image_client.pull(ref, ImagePullOptions(pathlib.Path(".")))

    @pytest.mark.parametrize("image_name, result", (("hello-world:error-tag", False), ("hello-world:latest", True)))
    def test_image_right_tag(self, image_client, image_name, result):
        assert image_client.exist(parse_normalized_named(image_name)) == result

    def test_get_tags(self, image_client, docker_hub_client):
        image_name = "library/hello-world"
        ref = parse_normalized_named(image_name)
        tags_by_api = image_client.list_tag(ref).json().get("tags", [])
        tags_by_http = docker_hub_client.list_tags(image_name)
        assert not set(tags_by_api) - set(tags_by_http)

    def test_tags_paginated_last(self, image_client):
        ref = parse_normalized_named("library/hello-world")
        tags = image_client.list_tag(ref, limit=1).json().get("tags", None)
        assert len(tags) == 1
        next_tags = image_client.list_tag(ref, limit=1, last=tags[-1]).json().get("tags", None)
        assert next_tags and next_tags != tags

    def test_get_dont_exists_image_tags(self, image_client):
        resp = image_client.list_tag(parse_normalized_named("library/hello-world1"))
        assert resp.status_code in (400, 401)

    @pytest.mark.parametrize(
        "host, image_name, target, want",
        (
            (DEFAULT_REGISTRY_HOST, "foo", "latest", "foo:latest"),
            (DEFAULT_REGISTRY_HOST, f"{DEFAULT_REPO}/foo", "latest", "foo:latest"),
            (DEFAULT_REGISTRY_HOST, f"{DEFAULT_REPO}1/foo", "latest", f"{DEFAULT_REPO}1/foo:latest"),
            ("a.com", "foo", "latest", "a.com/foo:latest"),
            ("a.com", "library/foo", "latest", "a.com/library/foo:latest"),
            ("a.com", "a/b/c/d/foo", "latest", "a.com/a/b/c/d/foo:latest"),
            (DEFAULT_REGISTRY_HOST, "foo", "digest1", None),
            (DEFAULT_REGISTRY_HOST, "library/foo", "digest2", None),
            (DEFAULT_REGISTRY_HOST, "a/b/c/foo", "digest3", None),
            ("a.com", "a/b/c/foo", "digest3", None),
        ),
    )
    def test_image_repo_tag(self, image_client, monkeypatch, random_digest, host, image_name, target, want):
        monkeypatch.setattr(image_client.client, "base_url", httpx.URL(f"https://{host}"))
        if target.startswith("digest"):
            target = f"@{random_digest.value}"
        else:
            target = f":{target}"
        ref_str = f"{host}/{image_name}{target}"
        print(ref_str)
        ref = parse_normalized_named(ref_str)
        assert image_client.repo_tag(ref) == want
