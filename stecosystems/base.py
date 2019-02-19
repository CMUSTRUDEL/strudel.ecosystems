
import logging

import requests
import six

import stscraper as scraper
import stutils
import stutils.decorators as d
from stutils import versions

TIMEOUT = stutils.get_config('PYPI_TIMEOUT', 10)
urlretrieve = six.moves.urllib.request.urlretrieve
logger = logging.getLogger('stecosystems')


class PackageDoesNotExist(ValueError):
    pass


class BasePackage(object):
    base_url = None
    name = None  # package name

    @classmethod
    def all(cls, **kwargs):
        # type: (**dict) -> BasePackage
        raise NotImplementedError

    def __init__(self, name, **kwargs):
        """
        Args:
            name (str): package name.
                Expect it will be normalized to prevent duplicate packages.
            info (Optional[dict]): in some cases (I'm talking about you, npm)
                ecosystem API lists packages with their metadata, so it's
                cheaper to reuse it.
                End users should not use this parameter.
        """
        self.name = name

    def __str__(self):
        return self.name

    def __repr__(self):
        return "<?? package: %s>" % self.name

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

    @property
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


def resolve_field(item, field, default=None):
    """Retrieve a field from JSON structure

    >>> item = {'one': 1, 'two': [1], 'three': {}}
    >>> resolve_field(item, 'one')
    1
    >>> resolve_field(item, 'two')
    1
    >>> resolve_field(item, 'three')
    {}
    >>> resolve_field(item, 'four') is None
    True
    """
    if not item:
        return default
    if isinstance(item, list):
        res = resolve_field(item[0], field)
    elif isinstance(item, six.string_types):
        res = item
    else:
        res = item.get(field, default)

    if isinstance(res, list):
        return res[0]
    return res


def json_path(item, *path):
    # type: (dict, *str) -> object
    """Helper function to traverse JSON

    >>> a = {'doc': {'versions': {'0.1': {'time': '2018-01-01T00:00.00.00Z'}}}}
    >>> json_path(a, 'doc', 'versions', '0.1', 'time')
    '2018-01-01T00:00.00.00Z'
    >>> json_path(a, 'doc', 'times', '0.1', 'time') is None
    True
    """
    res = item
    for key in path:
        try:  # this way is faster and supports list indexes
            res = res[key]
        except (IndexError, KeyError):
            return None
    return res
