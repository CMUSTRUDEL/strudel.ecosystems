#!/usr/bin/env python

""" An abstraction of Python package repository API (PyPi API).

"""

from __future__ import print_function

import json
import os
import re
import shutil
from xml.etree import ElementTree

from .base import *
from stutils import sysutils

DEFAULT_SAVE_PATH = '/tmp/pypi'
# directory where package archives are stored
PYPI_SAVE_PATH = stutils.get_config('PYPI_SAVE_PATH', DEFAULT_SAVE_PATH)
sysutils.mkdir(PYPI_SAVE_PATH)

logger = logging.getLogger("ghd.pypi")
fs_cache = d.fs_cache('pypi')

# path to provided shell scripts
_PATH = os.path.dirname(__file__) or '.'


def shell(cmd, *args, **kwargs):
    if kwargs.get('local', True):
        del kwargs['local']
        kwargs['rel_path'] = _PATH
    return sysutils.shell(cmd, *args, **kwargs)


# supported formats and extraction commands
UNZIP = 'unzip -qq -o "%(fname)s" -d "%(dir)s" 2>/dev/null'
UNTGZ = 'tar -C "%(dir)s" --strip-components 1 -zxf "%(fname)s" 2>/dev/null'
UNTBZ = 'tar -C "%(dir)s" --strip-components 1 -jxf "%(fname)s" 2>/dev/null'

SUPPORTED_FORMATS = {
    '.zip': UNZIP,
    '.whl': UNZIP,
    '.egg': UNZIP,  # can't find a single package to test. Are .eggs extinct?
    '.tar.gz': UNTGZ,
    '.tgz': UNTGZ,
    '.tar.bz2': UNTBZ,
    # rpm: contain directory structure from the root and thus can't be parsed
    # (same issue with bdist_dumb packages)
    # '.rpm': 'rpm2cpio "%(fname)s" | $(cd "%(dir)s" && cpio -i -d)'
}

"""
Notes:
1. There is no reliable source for supported Python version.
    requires-dist format is described here:
        https://www.python.org/dev/peps/pep-0345/#version-specifiers
    Unfortunately, it is not informative at all:
        - vast majority (99%) of packages doesn't use it
        - many of those which use do not conform to the standard
"""


class PackageDoesNotExist(ValueError):
    pass


def get_builtins(python_version):
    """ Return set of built-in libraries for Python2/3 respectively
    Intented for parsing imports from source files
    """
    assert python_version in (2, 3)
    url = "https://docs.python.org/%s/library/index.html" % python_version
    text = requests.get(url, timeout=TIMEOUT, verify=False).text
    # text is html and can't be processed with Etree, so regexp it is
    return set(b for b in re.findall(
        r"""<span\s+class=["']pre["']\s*>\s*([\w_-]+)\s*</span>""", text))


def python_loc_size(package_dir):
    """ Get LOC size of a given project

    The LOC count is parsed from pylint output, which includes this table:

    Raw metrics
    -----------

    +----------+-------+------+---------+-----------+
    |type      |number |%     |previous |difference |
    +==========+=======+======+=========+===========+
    |code      |4882   |61.13 |190351   |-185469.00 |
    +----------+-------+------+---------+-----------+
    |docstring |1134   |14.20 |37023    |-35889.00  |
    +----------+-------+------+---------+-----------+
    |comment   |282    |3.53  |17465    |-17183.00  |
    +----------+-------+------+---------+-----------+
    |empty     |1688   |21.14 |52558    |-50870.00  |
    +----------+-------+------+---------+-----------+

    In this example (pandas 0.1) we're looking for 4882
    """
    status, pylint_out = shell("pylint", "--py3k", package_dir,
                               local=False, raise_on_status=False,
                               stderr=open(os.devnull, 'w'))
    if status == 2:
        raise EnvironmentError("pylint is not installed (just in case, path is "
                               "%s)" % package_dir)
    m = re.search(r"\|code\s*\|([\s\d]+?)\|", pylint_out)
    match = m and m.group(1).strip()
    if not match:
        return 0
    return int(match)


class Package(BasePackage):
    base_url = "https://pypi.org"
    info = None  # stores cached package info
    _dirs = None  # created directories to cleanup later

    @classmethod
    def all(cls):
        tree = ElementTree.fromstring(cls._request("simple/").content)
        for package_name in sorted(a.text.lower() for a in tree.iter('a')):
            try:
                package = Package(package_name)
            except PackageDoesNotExist:
                continue
            else:
                yield package

    def __init__(self, name, **kwargs):
        try:
            self.info = self._request("pypi", self.name, "json").json()
        except IOError:
            raise PackageDoesNotExist(
                "Package %s does not exist on PyPi" % name)
        except ValueError:  # simplejson.scanner.JSONDecodeError is a subclass
            # malformed json
            raise ValueError("PyPi package description is invalid")

        self._dirs = []
        self.latest_ver = self.info['info'].get('version')
        super(Package, self).__init__(self.info['info']['name'])

    def __del__(self):
        if DEFAULT_SAVE_PATH != PYPI_SAVE_PATH:
            return
        for folder in self._dirs:
            try:
                # str conversion is required because of this shutil bug:
                # https://bugs.python.org/issue24672
                # use tai5_uan5_gian5_gi2_tsu1_liau7_khoo3-tng7_su5_piau1_im1
                # to test this issue
                shutil.rmtree(str(folder))
            except OSError:
                logger.debug("Error removing temp dir after package %s: %s",
                             self.name, folder)

    def __str__(self):
        return self.canonical_name

    def __repr__(self):
        return "<PyPi package: %s>" % self.name

    def releases(self, include_unstable=False, include_backports=False):
        """Return release labels
        :param include_unstable: bool, whether to include releases including
            symbols other than dots and numbers
        :param include_backports: bool, whether to include releases smaller in
            version than last stable release
        :return list of (label, date), sorted by date

        >>> len(Package("django").releases()) > 10
        True
        >>> len(Package("django").releases()[0])
        2
        >>> isinstance(Package("django").releases()[0], tuple)
        True
        """
        releases = sorted([
            (label, min(f['upload_time'][:10] for f in files))
            for label, files in self.info['releases'].items()
            if files],  # skip empty releases
            key=lambda r: r[1])  # sort by date

        if not include_unstable:
            releases = [(label, date)
                        for label, date in releases
                        if re.match(r"^\d+(\.\d+)*$", label)]

        if not include_backports and releases:
            _rel = []
            for label, date in releases:
                if not _rel or versions.compare(label, _rel[-1][0]) >= 0:
                    _rel.append((label, date))
            releases = _rel

        return releases

    def download_url(self, ver):
        """Get URL to package file of the specified version
        This function takes into account supported file types and their
        relative preference (e.g. wheel files before source packages)

        :param ver: str, version string
        :return: url string if found, None otherwise
        """
        assert ver in self.info['releases']
        # the rationale for iterating several times filtering out pkgtype:
        # some formats are more expensive to process, so it is basically
        # a preference order
        # NOT SUPPORTED: "bdist_dumb", "bdist_rpm", "bdist_deb", "bdist_wininst"
        #   bdist_dumb (contains file structure from root)
        #   bdist_rpm - need to add cpio command to extract content
        #   bdist_deb - not enough data / haven't seen any so far
        #   bdist_wininst is .exe files, most of the time
        # The last three remain on the list only because they
        # often contain source dist instead
        for pkgtype in ("bdist_wheel", "bdist_egg", "sdist",
                        "bdist_rpm", "bdist_deb", "bdist_wininst"):
            for info in self.info['releases'][ver]:
                if info['packagetype'] == pkgtype and \
                    any(info['url'].endswith(ext)
                        for ext in SUPPORTED_FORMATS):
                    return info['url']
        # no downloadable files in supported format
        logger.info("No downloadable files in supported formats "
                    "for package %s ver %s found", self.name, ver)
        return None

    @d.cached_method
    def download(self, ver=None):
        """Download and extract the specified package version from PyPi
        :param ver - Version of package
        """
        ver = ver or self.latest_ver
        logger.debug("Attempting to download package: %s", self.name)
        # ensure there is a downloadable package release
        download_url = self.download_url(ver)
        if download_url is None:
            return None

        # check if extraction folder exists
        extract_dir = os.path.join(PYPI_SAVE_PATH, self.name + "-" + ver)
        if os.path.isdir(extract_dir):
            if any(os.path.isdir(dirname)
                   for dirname in os.listdir(extract_dir)):
                logger.debug(
                    "Package %s was downloaded already, skipping", self.name)
                return extract_dir  # already extracted
        else:
            os.mkdir(extract_dir)
            self._dirs.append(extract_dir)

        # download file to the folder
        fname = os.path.join(extract_dir, download_url.rsplit("/", 1)[-1])
        try:  # TODO: timeout handling
            urlretrieve(download_url, fname)
        except IOError:  # missing file, very rare but happens
            logger.warning("Broken PyPi link: %s", download_url)
            return None

        # extract using supported format
        extension = ""
        for ext in SUPPORTED_FORMATS:
            if fname.endswith(ext):
                extension = ext
                break
        if not extension:
            raise ValueError("Unexpected archive format: %s" % fname)

        cmd = SUPPORTED_FORMATS[extension] % {
            'fname': fname, 'dir': extract_dir}
        os.system(cmd)

        # fix permissions (+X = traverse dirs)
        os.system('chmod -R u+rwX "%s"' % extract_dir)

        # edge case: zip source archives usually (always?) contain
        # extra level folder. If after extraction there is a single dir in the
        # folder, change extract_dir to that folder
        if download_url.endswith(".zip"):
            single_dir = None
            for entry in os.listdir(extract_dir):
                entry_path = os.path.join(extract_dir, entry)
                if os.path.isdir(entry_path):
                    if single_dir is None:
                        single_dir = entry_path
                    else:
                        single_dir = None
                        break
            if single_dir:
                extract_dir = single_dir

        return extract_dir

    def _info_path(self, ver):
        """
        :return: either xxx.dist-info or xxx.egg-info path, or None

        It is used by dependencies parser and to locate top_level.txt
        """
        extract_dir = self.download(ver)
        if not extract_dir:
            return None

        # hyphens are translated into underscores
        # multiple underscores are collapsed into one
        # there is only one package subject to this rule as of 06/2018
        # https://pypi.org/project/Tzara---A-Personal-Assistant/#files
        #
        # Quote: "Comparison of project names is case insensitive and treats
        # arbitrarily-long runs of underscores, hyphens, and/or periods
        # as equal."
        # This rule was defined in old PyPI Packaging User Guide, but it is not
        # in the actual version. It is quoted here:
        # https://github.com/pypa/pipenv/issues/1302
        cname = re.sub("[_-]+", "_", self.canonical_name)
        dist_info_path = "%s-%s.dist-info" % (cname, ver)
        egg_info_path = "%s.egg-info" % cname
        for info_path in (dist_info_path, egg_info_path, "EGG-INFO"):
            path = os.path.join(extract_dir, info_path)
            if os.path.isdir(path):
                logger.debug("Project has info folder: %s", path)
                return path
        logger.debug(
            "Neither dist-info nor egg-info folders found in %s", self.name)

    @d.cached_method
    def get_setup_params(self, extract_dir=None):
        extract_dir = extract_dir or self.download()
        if not os.path.isfile(os.path.join(extract_dir, 'setup.py')):
            return None
        _, output = shell("docker.sh", extract_dir)
        if not output.strip():
            logger.warning("Could not parse setup() params in %s", extract_dir)
            return None
        return json.loads(output)

    @d.cached_method
    def modules(self, ver=None):
        # type: (str) -> list
        """ Return list of modules provided by this package

        :param ver: str version
        :return: list of modules provided by this package

        For .egg and .whl they're stored in top_level.txt in dist-info path
        For source code, they are in setup() parameters:
            packages: - pure Python modules, the best possible case
            py_modules: - in case of single file packages
            ext_modules: - C extensions

        Tests:
            0.0.1[0.0.1] - tar.gz, dir
            0[0.0.0] - whl, single file (non-importable name)
            02exercicio[1.0.0] - tar.gz, no files
            asciaf[1.0.0] - tar.gz, no files
            0805nexter[1.2.0] - zip, single file
            4suite-xml[1.2.0] - tar.bz2, __init__ folder
            a3rt-sdk-py["0.0.3"] - folder not matching canonical name
            abofly["1.4.0"] - single file, using non-canonical name
        """
        ver = ver or self.latest_ver
        logger.debug("Package %s ver %s top folder:", self.name, ver)
        modules = []  # default return

        extract_dir = self.download(ver)
        if not extract_dir:
            return modules

        info_path = self._info_path(ver)

        def unique(*lists):
            # combine multiple iterables into one list with unique values
            return sorted(set().union(*lists))

        tl_fname = info_path and os.path.join(info_path, 'top_level.txt')
        # egg or wheel package
        if tl_fname and os.path.isfile(tl_fname):
            text = open(tl_fname, 'r').read(1024)
            return unique(
                (line.strip() for line in text.split() if line.strip()))

        # source package - check setup() parameters
        params = self.get_setup_params(extract_dir)
        if params is None:
            return modules
        # scripts are not importable and thus ignored here
        # perhaps they should be considered by module_paths
        keys = ('packages', 'py_modules', 'ext_modules', 'namespaces',
                'namespace_packages')
        return unique(*(params[key] for key in keys if key in params))

    @d.cached_method
    def module_paths(self, ver):
        """ Paths to dirs/files containing provided modules. """
        mod_paths = []
        extract_dir = self.download(ver)
        for path in self.modules(ver):
            if not os.path.isdir(os.path.join(extract_dir, path)):
                if os.path.isfile(os.path.join(extract_dir, path + ".py")):
                    path += ".py"
                else:
                    # a C extension (no path) or a malformed package
                    continue
            mod_paths.append(path)
        return mod_paths

    @d.cached_property
    def repository(self):
        """Search for a pattern in package info and package content
        Search places:
        - info home page field
        - full info page
        - package content
        :return url if found, None otherwise

        >>> Package("numpy").url
        'github.com/numpy/numpy'
        """
        # check home page first
        m = scraper.URL_PATTERN.search(
            self.info.get('info', {}).get('home_page', ""))
        if m:
            return m.group(0)

        pattern = scraper.named_url_pattern(self.name)

        m = re.search(pattern, str(self.info))
        if m:
            return m.group(0)

        for path in self.module_paths(self.latest_ver):
            _, output = shell("zgrep.sh", pattern, path, raise_on_status=False)
            output = output.strip()
            if output:
                return output
        return None

    @d.cached_method
    def dependencies(self, ver=None):
        """Extract dependencies from either wheels metadata or setup.py

        >>> 'numpy' in Package("pandas").dependencies()
        True
        """
        ver = ver or self.latest_ver
        default = {}  # default return vlaue
        logger.debug(
            "Getting dependencies for project %s ver %s", self.name, ver)
        extract_dir = self.download(ver)
        if not extract_dir:
            return default

        info_path = self._info_path(ver) or ""
        if info_path.endswith(".dist-info"):
            logger.debug("    .. WHEEL package, parsing from metadata.json")
            fname = os.path.join(info_path, 'metadata.json')
            if os.path.isfile(fname):
                info = json.load(open(fname))
                # only unconditional dependencies are considered
                # http://legacy.python.org/dev/peps/pep-0426/#dependency-specifiers
                deps = []
                for dep in info.get('run_requires', []):
                    if 'extra' not in dep and 'environment' not in dep:
                        deps.extend(dep['requires'])
            else:
                fname = os.path.join(info_path, 'METADATA')
                if not os.path.isfile(fname):
                    return default
                # example record:
                # Requires-Dist: numpy (>=1.9.0)
                # len("Requires-Dist:") == 14
                raw_deps = [line[14:].strip()
                            for line in open(fname)
                            if line.startswith("Requires-Dist:")]

                deps = []
                for raw_dep in raw_deps:
                    chunks = raw_dep.split(None, 1)
                    if len(chunks) == 1:
                        deps.append(raw_dep)
                        continue
                    chunks[1] = chunks[1].strip("()")
                    deps.append(" ".join(chunks))

        elif info_path.endswith(".egg-info"):
            logger.debug("    .. egg package, parsing requires.txt")
            fname = os.path.join(info_path, 'requires.txt')
            if not os.path.isfile(fname):
                return default
            deps = []
            for line in open(fname, 'r'):
                if "[" in line:
                    break
                if line:
                    deps.append(line)
        else:
            logger.debug("    ..generic package, running setup.py in a sandbox")
            params = self.get_setup_params(extract_dir)
            if params is None:
                logger.debug("    .. looks to be a malformed package")
                return default
            deps = params.get('install_requires', [])

        def dep_split(dependency):
            match = re.match(r"[\w_.-]+", dependency)
            if not match:  # invalid dependency
                name = ""
            else:
                name = match.group(0)
            version = dependency[len(name):].strip()
            return name, version

        return dict(dep_split(dep.strip()) for dep in deps if dep.strip())

    @d.cached_method
    def loc_size(self, ver):
        """get size in LOC"""
        return sum(python_loc_size(path) for path in self.module_paths(ver))
