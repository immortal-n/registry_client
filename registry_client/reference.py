#!/usr/bin/env python3
# encoding : utf-8
# create at: 2022/9/24-下午2:52
# https://github.com/distribution/distribution/blob/main/reference/regexp.go
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

import re2

from registry_client import errors
from registry_client.digest import Digest
from registry_client.utlis import DEFAULT_REGISTRY_HOST, DEFAULT_REPO

NameTotalLengthMax = 255
special_bytes = defaultdict(int)


def expression(*res: str):
    return "".join(res)


def group(*res: str):
    e = expression(*res)
    return f"(?:{e})"


def optional(*res: str):
    g = group(expression(*res))
    return f"{g}?"


def repeated(*res: str):
    return f"{group(expression(*res))}+"


def capture(*res: str):
    return f"({expression(*res)})"


def anchored(*res: str):
    return f"^{expression(*res)}$"


def literal(v: str):
    special = r"\.+*?()|[]{}^$"
    v = list(v)
    for index in range(len(v)):
        if v[index] in special:
            v[index] = f"\\{v[index]}"
    return "".join(v)


alpha_numeric = r"[a-z0-9]+"
separator = r"(?:[._]|__|[-]*)"
name_component = expression(alpha_numeric, optional(repeated(separator, alpha_numeric)))

domain_name_component = r"(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])"
ipv6_address = r"\[(?:[a-fA-F0-9:]+)\]"
domain_name = expression(domain_name_component, optional(repeated(literal("."), domain_name_component)))
host = f"(?:{domain_name}|{ipv6_address})"
domain = expression(
    host,
    optional(literal(":"), r"[0-9]+"),
)
DOMAIN_REGEXP = re2.compile(domain)

tag = r"[\w][\w.-]{0,127}"
TAG_REGEXP = re2.compile(tag)

ANCHORED_TAG_REGEXP = re2.compile(anchored(tag))

DIGEST_REGEXP = re2.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_+.][A-Za-z][A-Za-z0-9]*)*[:][[:xdigit:]]{32,}")
ANCHORED_DIGEST_REGEXP = re2.compile(anchored(DIGEST_REGEXP.pattern))

name_pat = expression(optional(domain, literal("/")), name_component, optional(repeated(literal("/"), name_component)))
anchored_name = anchored(
    optional(capture(domain), literal("/")),
    capture(name_pat, optional(repeated(literal("/"), name_pat))),
)
NAME_REGEXP = re2.compile(name_pat)
ANCHORED_NAME_REGEXP = re2.compile(anchored_name)

reference_pat = anchored(
    capture(name_pat), optional(literal(":"), capture(tag)), optional(literal("@"), capture(DIGEST_REGEXP.pattern))
)
REFERENCE_REGEXP = re2.compile(reference_pat)

IDENTIFIER_REGEXP = re2.compile(r"([a-f0-9]{64})")
SHORT_IDENTIFIER_REGEXP = re2.compile(r"([a-f0-9]{6,64})")
ANCHORED_IDENTIFIER_REGEXP = re2.compile(anchored(IDENTIFIER_REGEXP.pattern))
ANCHORED_SHORT_IDENTIFIER_REGEXP = re2.compile(anchored(SHORT_IDENTIFIER_REGEXP.pattern))


@dataclass
class Reference:
    domain: str = ""
    path: str = ""

    @property
    def name(self):
        if self.domain == "":
            return self.path
        return f"{self.domain}/{self.path}"

    def __str__(self):
        return self.name

    @property
    def target(self):
        raise NotImplementedError


@dataclass
class NamedReference(Reference):
    @property
    def target(self):
        return "latest"


@dataclass
class TaggedReference(Reference):
    @property
    def target(self):
        return self.tag

    tag: str = ""

    def __str__(self):
        return f"{self.name}:{self.tag}"


class DigestReference(Reference):
    @property
    def target(self):
        return self.digest.value

    def __init__(self, digest: Digest):
        self.digest = digest

    def __str__(self):
        return self.digest.value


@dataclass
class CanonicalReference(Reference):
    digest: Optional[Digest] = None

    @property
    def target(self):
        return self.digest.value

    def __str__(self):
        return f"{self.name}@{self.digest}"


@dataclass
class FullReference(NamedReference):
    tag: str = None
    digest: Digest = None

    def __str__(self):
        if self.domain == "":
            name = self.path
        else:
            name = f"{self.domain}/{self.path}"
        return f"{name}:{self.tag}@{self.digest.value}"


def parse(name: str) -> Reference:
    """
    Parse parses s and returns a syntactically valid Reference.
    If an error was encountered it is returned, along with a nil Reference.
    NOTE: Parse will not handle short digests.
    :param name:
    :return:
    """
    result = REFERENCE_REGEXP.findall(name)
    if not result:
        if name == "":
            raise errors.ErrNameEmpty()
        if not name.islower():
            raise errors.ErrNameContainsUppercase()
        raise errors.ErrReferenceInvalidFormat()
    result = result[0]
    if len(result[0]) > NameTotalLengthMax:
        raise errors.ErrNameTooLong()
    name_match = ANCHORED_NAME_REGEXP.findall(result[0])[0]
    if len(name_match) == 2:
        domain, path = name_match
    else:
        domain = ""
        path = name_match[-1]
    repo = NamedReference(domain, path)
    tag = ""
    digest = None
    if result[1]:
        tag = result[1]
    if result[2]:
        if not Digest.is_digest(result[2]):
            raise Exception(f"invalid digest format: {result[2]}")
        digest = Digest(result[2])
    if repo.name == "":
        if digest:
            return DigestReference(digest)
        raise errors.ErrNameEmpty()
    if tag == "":
        if digest:
            return CanonicalReference(domain=domain, path=path, digest=digest)
        return NamedReference(domain=domain, path=path)
    if digest is None:
        return TaggedReference(domain=domain, path=path, tag=tag)
    return FullReference(domain, path, tag, digest)


def split_docker_domain(name: str):
    index = name.find("/")
    if index == -1 or (not re2.findall(r"[.:]", name[:index]) and name[:index] != "localhost"):
        domain, remainder = DEFAULT_REGISTRY_HOST, name
    else:
        domain, remainder = name[:index], name[index + 1 :]
    if domain == "index.docker.io":
        domain = DEFAULT_REGISTRY_HOST
    if domain == DEFAULT_REGISTRY_HOST and "/" not in remainder:
        remainder = f"{DEFAULT_REPO}/{remainder}"
    return domain, remainder


def split_domain(name: str):
    match = ANCHORED_NAME_REGEXP.findall(name)
    if len(match[0]) != 2:
        return "", name
    return match[0]


def parse_normalized_named(name: str) -> Reference:
    if re2.match(r"^([a-f0-9]{64})$", name):
        raise Exception(f"invalid repository name ({name}), cannot specify 64-byte hexadecimal strings")
    domain, remainder = split_docker_domain(name)
    if remainder.find(":") != -1:
        remote_name = remainder.partition(":")[0]
    else:
        remote_name = remainder
    if not remote_name.islower():
        raise Exception("invalid reference format: repository name must be lowercase")
    return parse(f"{domain}/{remainder}")
