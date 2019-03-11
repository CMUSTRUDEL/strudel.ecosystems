#!/usr/bin/env python

"""
API docs:
    https://github.com/npm/registry/blob/master/docs/REGISTRY-API.md
API base URL:
    http://registry.npmjs.com/

extra metrics:
    https://api-docs.npms.io/#api-Package-GetMultiPackageInfo

"""

from __future__ import print_function

import urllib

import ijson.backends.yajl2 as ijson

from .base import *
from stutils import decorators as d

fs_cache = d.fs_cache('npm')


class Package(BasePackage):
    base_url = 'http://registry.npmjs.com/'
    info = None  # stores cached package info

    @classmethod
    def all(cls, cache_file=None):
        if isinstance(cache_file, six.string_types):
            fh = open(cache_file, 'rb')
        elif isinstance(cache_file, six.StringIO):
            fh = cache_file
        else:
            # how to create cache file: wget -O npm.json <url below>
            # it is 14Gb as of Jan 2019
            fh = urllib.urlopen(
                'https://skimdb.npmjs.com/registry/_all_docs?include_docs=true')

        for package_info in ijson.items(fh, 'rows.item'):
            package_name = package_info['id']
            yield Package(package_name, info=package_info['doc'])

    def __init__(self, name, info=None):
        """
        Args:
            name (str): package name.
                Expect it will be normalized to prevent duplicate packages.
            info (Optional[dict]): in some cases (I'm talking about you, npm)
                ecosystem API lists packages with their metadata, so it's
                cheaper to reuse it.
                End users should not use this parameter.
        """
        if info:
            self.info = info
        else:
            try:
                self.info = self._request(name).json()
            except IOError:
                raise PackageDoesNotExist(
                    "Package %s does not exist in npm" % name)

        super(Package, self).__init__(name)

    @d.cached_property
    def _extra_info(self):
        return requests.get(
            "https://api.npms.io/v2/package/" + self.name).json()

    @property
    def quality(self):
        return json_path(self._extra_info, 'score', 'detail', 'quality')

    @property
    def popularity(self):
        return json_path(self._extra_info, 'score', 'detail', 'popularity')

    @property
    def maintenance_score(self):
        return json_path(self._extra_info, 'score', 'detail', 'maintenance')

    @classmethod
    def _request(cls, *path):
        if path and not (path[0].startswith('https://') or path[0].startswith('http://')):
            path = (cls.base_url,) + path

        for _ in range(3):
            try:
                r = requests.get("/".join(path), timeout=TIMEOUT)
            except requests.exceptions.Timeout:
                continue
            r.raise_for_status()
            return r
        raise IOError("Failed to reach %s. "
                      "Check your Internet connection." % cls.base_url)

    def releases(self, include_unstable=False, include_backports=False):
        """ Return package release labels

        Args:
            include_unstable (bool): whether to include releases including
                symbols other than dots and numbers.
            include_backports (bool): whether to include releases smaller in
                version than last stable release

        Returns:
             List[Tuple[str, str]]: (label, date), sorted by date
        """
        raise NotImplementedError

    def download_url(self, ver):
        """Get URL to package file of the specified version
        This function takes into account supported file types and their
        relative preference (e.g. wheel files before source packages)

        Args:
            ver (str): version string

        Returns:
            Optional[str]: url string if found, None otherwise
        """
        assert ver in self.info['releases']
        raise NotImplementedError

    def download(self, ver=None):
        """Download and extract the specified package version

        Args:
             ver (str): Version of the package

        Returns:
            Optional[str]: path to the folder with extracted package,
                None if download failed
        """
        raise NotImplementedError

    @d.cached_property
    def repository(self):
        """ Search for software repository URL

        Returns:
            Optional[str]: path to the folder with extracted package,
                None if download failed
        """
        raise NotImplementedError

    def dependencies(self, ver=None):
        """ Get technical dependencies

        Args:
            ver (Optional[str]): version string, latest version by default

        Returns:
            Dict[str, str]: dictionary of the form `{package: version}`;
                `version` might contain platform-specific modifiers, e.g.
                '>=3.12.1'. If not specified, `version will be `None`.
        """
        raise NotImplementedError

    def loc_size(self, ver):
        """ Get package size in LOC """
        raise NotImplementedError
