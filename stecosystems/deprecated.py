
from collections import defaultdict
import json
import os

import pandas as pd

import stutils.email_utils as email
import stscraper as scraper
from stutils import decorators as d
from stutils import mapreduce

from .base import *
from . import pypi
from . import npm

fs_cache = d.fs_cache('npm')


def pypi_packages_info():
    """
    :return: a pd.Dataframe with columns:
        - author: author email, str
        - url: a str suitable for scraper.parse_url() or scraper.get_provider()
        - license: unstructured str to be used with common.utils.parse_license()

    """
    names = []  # list of package names
    urls = {}  # urls[pkgname] = github_url
    authors = {}  # authors[pkgname] = author_email
    licenses = {}
    author_projects = defaultdict(list)
    author_orgs = defaultdict(
        lambda: defaultdict(int))  # orgs[author] = {org: num_packages}

    for package_name in pypi.Package.all():
        logger.info("Processing %s", package_name)
        try:
            p = pypi.Package(package_name)
        except pypi.PackageDoesNotExist:
            # some deleted packages aren't removed from the list
            continue
        names.append(package_name)

        if p.repository:
            urls[package_name] = p.repository

        try:
            author_email = email.clean(p.info["info"].get('author_email'))
        except email.InvalidEmail:
            author_email = None

        if author_email:
            author_projects[author_email].append(package_name)

        authors[package_name] = author_email
        licenses[package_name] = p.info['info']['license']

        if p.repository:
            provider, project_url = scraper.parse_url(p.repository)
            if provider == "github.com":
                org, _ = project_url.split("/")
                author_orgs[author_email][org] += 1

    # at this point, we have ~54K repos
    # by guessing github account from author affiliations we can get 8K more
    processed = 1
    total = len(author_projects)
    for author, packages in author_projects.items():
        logger.info("Postprocessing authors (%d out of %d): %s",
                    processed, total, author)
        processed += 1
        # check all orgs of the author, starting from most used ones
        orgs = [org for org, _ in
                sorted(author_orgs[author].items(), key=lambda x: -x[1])]
        if not orgs:
            continue
        for package in packages:
            if package in urls:
                continue
            for org in orgs:
                url = "%s/%s" % (org, package)
                r = requests.get("https://github.com/" + url)
                if r.status_code == 200:
                    urls[package] = url
                    break

    return pd.DataFrame({"url": urls, "author": authors, 'license': licenses},
                        index=names)


def pypi_dependencies():
    """ Get a bunch of information about npm packages
    This will return pd.DataFrame with package name as index and columns:
        - version: version of release, str
        - date: release date, ISO str
        - deps: names of dependencies, comma separated string
        - raw_dependencies: dependencies, JSON dict name: ver
        - raw_test_dependencies
        - raw_build_dependencies
    """
    deps = {}
    fname = fs_cache.get_cache_fname(".deps_and_size.cache")

    if os.path.isfile(fname):
        logger.info("deps_and_size() cache file already exists. "
                    "Existing records will be reused")

        def gen(df):
            d = {}
            for index, row in df.iterrows():
                item = row.to_dict()
                item["name"] = index[0]
                item["version"] = index[1]
                d[tuple(index)] = item
            return d

        deps = gen(pd.read_csv(fname, index_col=["name", "version"]))

    else:
        logger.info("deps_and_size() cache file doesn't exists. "
                    "Computing everything from scratch is a lengthy process "
                    "and will likely take a week or so")

    tp = mapreduce.ThreadPool()
    logger.info("Starting a threadppol with %d workers...", tp.n)

    package_names = pypi_packages_info().index

    def do(pkg_name, ver, release_date):
        # this method is used by worker threads, calling done() on finish
        p_deps = pypi.Package(pkg_name).dependencies(ver)

        return {
            'name': pkg_name,
            'version': ver,
            'date': release_date,
            'deps': ",".join(p_deps.keys()).lower(),
            'raw_dependencies': json.dumps(p_deps)
        }

    def done(output):
        deps[(output["name"], output["version"])] = output

    for package_name in package_names:
        logger.info("Processing %s", package_name)
        try:
            p = pypi.Package(package_name)
        except pypi.PackageDoesNotExist:
            continue

        for version, release_date in p.releases(True, True):
            if (package_name, version) not in deps:
                logger.info("    %s", version)
                tp.submit(do, package_name, version, release_date, callback=done)
            else:
                logger.info("    %s (cached)", version)

    # wait for workers to complete
    tp.shutdown()

    # save updates
    df = pd.DataFrame(deps.values()).sort_values(["name", "version"]).set_index(
        ["name", "version"], drop=True)
    df.to_csv(fname)

    return df


@fs_cache
def npm_packages_info():
    # type: () -> pd.DataFrame
    """ Get a bunch of information about npm packages
    This will return pd.DataFrame with package name as index and columns:
        - url: date of release, YYYY-MM-DD str
        - version: version of release, str
        - deps: dependencies, comma separated string
        - owners
    """

    def gen():
        logger = logging.getLogger("npm.utils.package_info")
        for package in npm.Package.all():
            logger.info("Processing %s", package['key'])
            # TODO: before falling back to str(package), use named pattern
            repo = npm._get_field(package['doc'].get('repository'), 'url') or \
                npm._get_field(package['doc'].get('homepage'), 'url') or \
                npm._get_field(package['doc'].get('bugs'), 'url') or str(package)

            m = repo and scraper.URL_PATTERN.search(repo)

            yield {
                'name': package['key'],
                'url': m and m.group(0),
                'author': npm._get_field(package['doc'].get('author', {}), 'email'),
                'license': npm.json_path(package, 'doc', 'license')
            }

    return pd.DataFrame(gen()).set_index('name', drop=True)


def npm_dependencies():
    """ Get a bunch of information about npm packages
    This will return pd.DataFrame with package name as index and columns:
        - version: version of release, str
        - date: release date, ISO str
        - deps: names of dependencies, comma separated string
        - raw_dependencies: dependencies, JSON dict name: ver
    """

    def gen():
        logger = logging.getLogger("npm.utils.package_info")
        for package in npm.Package.all():
            logger.info("Processing %s", package['key'])
            # possible sources of release date:
            # - ['doc']['time'][<ver>] - best source, sometimes missing
            # - ['doc']['versions'][<ver>]['ctime|mtime']  # e.g. Graph
            # - ['doc']['time']['modified|created'] # e.g. stack-component
            # - ['doc']['ctime|mtime']  # e.g. Lingo
            # - empty  # JSLint-commonJS

            for version, release in package['doc'].get('versions', {}).items():
                deps = release.get('dependencies') or {}
                deps = {dep.decode("utf8"): ver
                        for dep, ver in deps.items()}
                time = npm.json_path(package, 'doc', 'time', version) or \
                    npm.json_path(release, 'ctime') or \
                    npm.json_path(release, 'mtime') or \
                    npm.json_path(package, 'doc', 'time', 'created') or \
                    npm.json_path(package, 'doc', 'time', 'modified') or \
                    None

                yield {
                    'name': package['key'],
                    'version': version,
                    'date': time,
                    'deps': ",".join(deps.keys()),
                    'raw_dependencies': json.dumps(deps)
                }

    return pd.DataFrame(gen()).sort_values(
        ['name', 'date']).set_index('name', drop=True)
