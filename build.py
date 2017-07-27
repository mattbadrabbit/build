#!/usr/local/bin/python3.6
import sys
import argparse
import yaml
import subprocess
import tempfile
import logging
import os
import shutil
import pathlib
from jinja2 import Environment, FileSystemLoader, select_autoescape


logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser()
parser.add_argument("-l", "--log", help="Log level")
parser.add_argument("-a", "--app", help="Specify the app to build", required=True)
parser.add_argument("-f", "--flavor", help="Specify the flavor of the app", required=True)
parser.add_argument("-v", "--version", help="The tag/version of the app to build")

def fatal_error(s, exit_code=1):
    print(s, file=sys.stderr)
    exit(exit_code)

if __name__ == "__main__":
    args = parser.parse_args()
    if args.log:
        logging.basicConfig(stream=sys.stdout)
        logger.setLevel(args.log)

    with open("builds.yaml", 'r') as stream:
        try:
            builds = yaml.load(stream)
            print(builds)
        except yaml.YAMLError as exc:
            print(exc)
            exit(1)

    # argument checking
    if args.app not in builds:
        fatal_error("App with name '%s' not found in builds.yaml. Choices are: %s" % (args.app, ", ".join(builds.keys())))

    app = args.app

    build = builds[args.app]
    flavors = build.get("flavors", [])
    if args.flavor not in flavors:
        fatal_error("Flavor with name '%s' not found for the '%s' app. Choices are: %s" % (args.flavor, args.app, ", ".join(flavors)))


    if not build.get("repo"):
        fatal_error("The 'repo' key is missing from the YAML for the %s app" % (args.app))

    # We build a directory which will eventually include all the stuff we need to build the BSD image
    # It includes the interpolated customfiles (with the repo's source code) and packages
    repo = build['repo']
    with tempfile.TemporaryDirectory(suffix=None, prefix="build-" + args.app + "-", dir=None) as tmp_dir:
        tmp_dir = pathlib.Path(tmp_dir)

        # start templating the custom files
        custom_files_dir = tmp_dir / "customfiles"
        shutil.copytree("customfiles", custom_files_dir)
        env = Environment(
            loader=FileSystemLoader([custom_files_dir]),
            autoescape=select_autoescape(['html', 'xml'])  # probably not needed?
        )

        # walk all the custom files, and customize them with Jinja
        for d, dirs, files in os.walk(custom_files_dir):
            for f in files:
                full_path = os.path.join(d, f)
                rel_path = os.path.relpath(full_path, custom_files_dir)
                template = env.get_template(rel_path)
                with open(full_path, "w") as f:
                    f.write(template.render(the='variables', go='here'))

        # now do all the git stuff
        git_dir = tmp_dir / "src"
        os.mkdir(git_dir)
        repo_cmd = lambda *args, shell=False: subprocess.call(args, cwd=git_dir, shell=shell)

        # clone the repo
        logger.debug("Checking out the repo")
        status = repo_cmd("git", "clone", repo, git_dir)
        if status != 0:
            fatal_error("Could not checkout the repo " + repo)

        # checkout the right version
        if args.version:
            logger.debug("Doing a git checkout for the tag")
            status = repo_cmd("git", "checkout", args.version)
            if status != 0:
                fatal_error("Could not checkout the version of the repo " + args.version)

        # run the build script
        if build.get("script"):
            logger.debug("Running the build script")
            status = repo_cmd(build['script'], shell=True)
            if status != 0:
                fatal_error("The build script blew up. Tried to execute: \n" + build['script'])

        # copy the source code into opt
        opt_dir = custom_files_dir / "opt" / app
        os.makedirs(opt_dir)
        os.rename(git_dir, opt_dir)

        # now we need to fetch all the packages required for this app
