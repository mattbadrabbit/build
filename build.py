#!/usr/local/bin/python3.6
"""
This generates a bootable ISO for a BR git project

At a high level, we
- clone the git repo
- checkout whatever version you want to build
- run a provisioning script (that is specific to the repo), if one is defined
- Interpolate all the customfiles with settings from builds.yaml
- Use mfsbsd to build the iso
"""
import io
from datetime import datetime
import sys
import argparse
from urllib.parse import urlparse
import yaml
import subprocess
import tempfile
import logging
import os
import shutil
import pathlib
from jinja2 import Environment, FileSystemLoader, select_autoescape


base_iso = "FreeBSD-11.0-RELEASE-amd64-disc1.iso"

logger = logging.getLogger(__name__)


def render_to_file(source, dest, **variables):
    """Use jinja to template source, and write it to dest"""
    # start templating the custom files
    dir_ = os.path.dirname(source)
    env = Environment(
        loader=FileSystemLoader([dir_]),
        autoescape=select_autoescape(['html', 'xml'])  # probably not needed?
    )

    rel_path = os.path.relpath(source, dir_)
    template = env.get_template(rel_path)
    with open(dest, "w") as f:
        f.write(template.render(**variables))


def fatal_error(s, exit_code=1):
    print(s, file=sys.stderr)
    exit(exit_code)


if __name__ == "__main__":
    here = pathlib.Path(os.path.abspath(os.path.dirname(__file__)))

    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--log", help="Log level")
    parser.add_argument("-r", "--repo", help="Path/URL to the repo", default=".")
    parser.add_argument("-I", "--in-place", help="If --repo is a file path, this does repo operations in place", default=".")
    parser.add_argument("-f", "--flavor", help="Specify the flavor of the app", required=True)
    parser.add_argument("-v", "--version", help="The tag/version of the app to build")
    parser.add_argument("-p", "--pause", help="Pause before the mfs make process, so you can inspect the tmp dir")

    args = parser.parse_args()
    if args.log:
        logging.basicConfig(stream=sys.stdout)
        logger.setLevel(args.log)

    repo_parts = urlparse(args.repo)
    if args.in_place and repo_parts.scheme != "file":
        fatal_error("If you use the -in-place/-I option, the --repo/-r argument must start with file://")


    # We build a directory which will eventually include all the stuff we need to build the BSD image
    # It includes the interpolated customfiles (with the repo's source code) and packages
    repo = args.repo
    with tempfile.TemporaryDirectory(suffix=None, prefix="build-" + args.app + "-", dir=None) as tmp_dir:
        tmp_dir = pathlib.Path(tmp_dir)

        git_dir = tmp_dir / "src" if not args.in_place else pathlib.Path(repo_parts.netloc)
        os.makedirs(git_dir)
        repo_cmd = lambda *args, **kwargs: subprocess.call(args, cwd=git_dir, **kwargs)

        # clone the repo, or do a fetch, if we already have a .git dir
        if (git_dir / ".git").is_dir():
            logger.debug("Fetching the repo")
            status = repo_cmd("git", "fetch", "--tags")
            if status != 0:
                fatal_error("Could not fetch the repo " + repo)
        else:
            logger.debug("Cloning the repo")
            status = repo_cmd("git", "clone", repo, git_dir)
            if status != 0:
                fatal_error("Could not clone the repo " + repo)

        # checkout the right version
        if args.version:
            logger.debug("Doing a git checkout for the tag")
            status = repo_cmd("git", "checkout", args.version)
            if status != 0:
                fatal_error("Could not checkout the version of the repo " + args.version)

        # get the git hash
        output = open(tmp_dir / "hash.txt", "w+")
        repo_cmd("git", "rev-parse", "--short", "HEAD", stdout=output)
        output.seek(0)
        git_hash = output.read().strip()

        version_name = args.version or (datetime.now().strftime("%Y-%m-%d-") + git_hash)

        # figure out what we're building
        try:
            with open(git_dir / "builds.yaml", 'r') as stream:
                try:
                    builds = yaml.load(stream)
                except yaml.YAMLError as exc:
                    print(exc)
                    exit(1)
        except FileNotFoundError:
            fatal_error("Did not find a builds.yaml file in the directory " + git_dir)

        flavors = builds.get("flavors", {})
        if args.flavor not in flavors:
            fatal_error("Flavor with name '%s' not found. Choices are: %s" % (args.flavor, ", ".join(flavors)))
        flavor = build['flavors'][args.flavor]

        # run the build script
        if builds.get("script"):
            logger.debug("Running the build script")
            status = repo_cmd(builds['script'], shell=True)
            if status != 0:
                fatal_error("The build script blew up. Tried to execute: \n" + builds['script'])

        custom_files_dir = tmp_dir / "customfiles"
        shutil.copytree(here / "customfiles", custom_files_dir)
        # merge in the git repo's custom files

        # walk all the custom files, and customize them with Jinja
        for d, dirs, files in os.walk(custom_files_dir):
            for f in files:
                full_path = os.path.join(d, f)
                render_to_file(full_path, full_path, **flavor, app_name=app, flavor_name=args.flavor)

        # now we need to fetch all the packages required for this app
        # TODO package file caching
        packages_file_path = builds.get("packages")
        packages_dir = tmp_dir / "packages"
        packages = set()
        os.mkdir(packages_dir)
        if packages_file_path:
            try:
                f = open(git_dir / packages_file_path, "r")
            except FileNotFoundError:
                fatal_error("builds.yaml claimed there was a packages file named '%s' in the source code. There was not. Check the builds.yaml file to make sure the 'packages' property has the correct file path, and check the source code to make sure that file exists." % packages_file_path)

            for package in f:
                package = package.strip()
                if package:
                    packages.add(package)

        # add in the base packages
        [packages.add(p.strip()) for p in open("packages.txt") if p.strip()]
        # fetch the packages
        for package in packages:
            status = subprocess.call(["pkg", "fetch", "--yes", "--dependencies", "--output", packages_dir, package])
            if status != 0:
                fatal_error("Could not install the package " + package)

        # copy the source code into opt
        opt_dir = custom_files_dir / builds.get('app_dir', "/opt/app").lstrip("/")
        os.makedirs(opt_dir)
        #os.rename(git_dir, opt_dir)
        # shutil.copytree is crappy
        subprocess.call(["cp", "-r", git_dir + "/.", opt_dir])
        #shutil.copytree(git_dir, opt_dir)
        # don't need the .git dir
        shutil.rmtree(opt_dir / ".git")
        shutil.rmtree(opt_dir / ".git")

        # now build the image

        # setup the memory disk with the ISO. This ISO contains the kernel
        memory_disk_number = 10  # doesn't really matter what the number is. It just can't be in use
        memory_disk_path = pathlib.Path("/dev") / ("md" + str(memory_disk_number))
        cd_rom_path = pathlib.Path("/tmp/cdrom")
        if not os.path.exists(memory_disk_path):
            subprocess.call(["mdconfig -a -t vnode -u 10 -f " + base_iso], shell=True)
            subprocess.call(["mkdir -p " + str(cd_rom_path)], shell=True)
            subprocess.call(["mount_cd9660", memory_disk_path, cd_rom_path])

        # mfs stuff stuff
        mfs_dir = here / "mfsbsd-2.3/"
        mfs_cmd = lambda *args, **kwargs: subprocess.call(*args, cwd=mfs_dir, **kwargs)
        # clean up first
        mfs_cmd(["make", "clean"])
        mfs_cmd(["rm -f *.iso"], shell=True)
        # There is a bug (or gotcha) in mfs with the PACKAGESDIR. The PACKAGESDIR path must end in "packages".
        # The way freebsd installs packages is, it puts the files in a directory called "All". So we rename that
        # to "packages" to make it work with mfs
        free_bsd_package_path = packages_dir / "All"
        mfs_package_path = packages_dir / "packages"
        os.rename(free_bsd_package_path, mfs_package_path)

        if args.pause:
            input("Pausing so you can look at " + str(tmp_dir))

        # and finally...make the iso
        mfs_cmd([
            "make",
            "iso",
            "BASE=" + str(cd_rom_path / "usr/freebsd-dist/"),
            "PKG_STATIC=/usr/local/sbin/pkg-static",
            "MFSROOT_MAXSIZE=250m",
            "PACKAGESDIR=" + str(mfs_package_path),
            "CUSTOMFILESDIR=" + str(custom_files_dir),
        ])

        # mv the ISO to a better name
        iso_name = custom_files_dir / "iso.iso"
        ova_name = here / ".".join([app, args.flavor, version_name, "ova"])
        ovf_name = custom_files_dir / "template.ovf"
        ovf_template = here / "template.ovf"
        mfs_cmd(["mv *.iso " + str(iso_name)], shell=True)
        render_to_file(ovf_template, ovf_name, **flavor, app_name=app, flavor_name=args.flavor, version_name=version_name)
        # ustar is required to make this work...who knew there were multiple flavors of tar?
        subprocess.call(["tar", "--format=ustar", "--create", "--file", ova_name, "--directory", custom_files_dir, ovf_name.name, iso_name.name])
