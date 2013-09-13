# -*- mode: python; encoding: utf-8 -*-
#
# Copyright 2013 Jens Lindström, Opera Software ASA
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy of
# the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.  See the
# License for the specific language governing permissions and limitations under
# the License.

import sys
import os
import argparse
import logging
import re
import subprocess
import traceback

import testing

def main():
    parser = argparse.ArgumentParser(description="Critic testing framework")
    parser.add_argument("--debug", help="Enable DEBUG level logging", action="store_true")
    parser.add_argument("--quiet", help="Disable INFO level logging", action="store_true")
    parser.add_argument("--coverage", help="Enable coverage measurement mode", action="store_true")
    parser.add_argument("--commit", help="Commit (symbolic ref or SHA-1) to test [default=HEAD]", default="HEAD")
    parser.add_argument("--upgrade-from", help="Commit (symbolic ref or SHA-1) to install first and upgrade from")
    parser.add_argument("--strict-fs-permissions", help="Set strict file-system permissions in guest OS", action="store_true")
    parser.add_argument("--vbox-host", help="Host that's running VirtualBox [default=host]", default="host")
    parser.add_argument("--vm-identifier", help="VirtualBox instance name or UUID", required=True)
    parser.add_argument("--vm-hostname", help="VirtualBox instance hostname [default=VM_IDENTIFIER]")
    parser.add_argument("--vm-snapshot", help="VirtualBox snapshot (name or UUID) to restore [default=clean]", default="clean")
    parser.add_argument("--vm-ssh-port", help="VirtualBox instance SSH port [default=22]", type=int, default=22)
    parser.add_argument("--vm-http-port", help="VirtualBox instance HTTP port [default=80]", type=int, default=80)
    parser.add_argument("--git-daemon-port", help="Port to tell 'git daemon' to bind to", type=int)
    parser.add_argument("--pause-before", help="Pause testing before specified test(s)", action="append")
    parser.add_argument("--pause-after", help="Pause testing before specified test(s)", action="append")
    parser.add_argument("--pause-on-failure", help="Pause testing after each failed test", action="store_true")
    parser.add_argument("test", help="Specific tests to run [default=all]", nargs="*")

    arguments = parser.parse_args()

    class Counters:
        def __init__(self):
            self.tests_run = 0
            self.tests_failed = 0
            self.errors_logged = 0
            self.warnings_logged = 0

    counters = Counters()

    class CountingLogger(object):
        def __init__(self, real, counters):
            self.real = real
            self.counters = counters

        def log(self, level, message):
            if level == logging.ERROR:
                self.counters.errors_logged += 1
            elif level == logging.WARNING:
                self.counters.warnings_logged += 1
            for line in message.splitlines() or [""]:
                self.real.log(level, line)
        def debug(self, message):
            self.log(logging.DEBUG, message)
        def info(self, message):
            self.log(logging.INFO, message)
        def warning(self, message):
            self.log(logging.WARNING, message)
        def error(self, message):
            self.log(logging.ERROR, message)
        def exception(self, message):
            self.log(logging.ERROR, message + "\n" + traceback.format_exc())

    logger = testing.configureLogging(
        arguments, wrap=lambda logger: CountingLogger(logger, counters))
    logger.info("Critic Testing Framework")

    import_errors = False

    try:
        import requests
    except ImportError:
        logger.error("Failed to import 'requests'!")
        import_errors = True

    try:
        import BeautifulSoup
    except ImportError:
        logger.error("Failed to import 'BeautifulSoup'!")
        import_errors = True

    if import_errors:
        logger.error("Required software missing; see testing/USAGE.md for details.")
        return

    # Note: we are not ignoring typical temporary editor files such as the
    # ".#<name>" files created by Emacs when a buffer has unsaved changes.  This
    # is because unsaved changes in an editor is probably also something you
    # don't want to test with.

    locally_modified_paths = []

    status_output = subprocess.check_output(
        ["git", "status", "--porcelain"])

    for line in status_output.splitlines():
        locally_modified_paths.extend(line[3:].split(" -> "))

    tests_modified = []
    input_modified = []
    other_modified = []

    for path in locally_modified_paths:
        if path.startswith("testing/input/"):
            input_modified.append(path)
        elif path.startswith("testing/"):
            tests_modified.append(path)
        else:
            other_modified.append(path)

    if input_modified:
        logger.error("Test input files locally modified:\n  " + "\n  ".join(input_modified))
    if other_modified:
        logger.error("Critic files locally modified:\n  " + "\n  ".join(other_modified))
    if input_modified or other_modified:
        logger.error("Please commit or stash local modifications before running tests.")
        return

    if tests_modified:
        logger.warning("Running tests using locally modified files:\n  " + "\n  ".join(tests_modified))

    tested_commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", arguments.commit]).strip()

    if arguments.upgrade_from:
        install_commit = subprocess.check_output(
            ["git", "rev-parse", "--verify", arguments.upgrade_from]).strip()
        upgrade_commit = tested_commit
    else:
        install_commit = tested_commit
        upgrade_commit = None

    install_commit_description = subprocess.check_output(
        ["git", "log", "--oneline", "-1", install_commit]).strip()

    if upgrade_commit:
        upgrade_commit_description = subprocess.check_output(
            ["git", "log", "--oneline", "-1", upgrade_commit]).strip()
    else:
        upgrade_commit_description = None

    try:
        frontend = testing.frontend.Frontend(
            hostname=arguments.vm_hostname or arguments.vm_identifier,
            http_port=arguments.vm_http_port)

        instance = testing.virtualbox.Instance(
            vboxhost=arguments.vbox_host,
            identifier=arguments.vm_identifier,
            snapshot=arguments.vm_snapshot,
            hostname=arguments.vm_hostname,
            ssh_port=arguments.vm_ssh_port,
            install_commit=(install_commit, install_commit_description),
            upgrade_commit=(upgrade_commit, upgrade_commit_description),
            frontend=frontend,
            strict_fs_permissions=arguments.strict_fs_permissions,
            coverage=arguments.coverage)
    except testing.Error as error:
        logger.error(error.message)
        return

    if arguments.test:
        tests = set(test.strip("/") for test in arguments.test)
        groups = {}

        for test in sorted(tests):
            components = test.split("/")
            if test.endswith(".py"):
                del components[-1]
            else:
                test += "/"
            for index in range(len(components)):
                groups.setdefault("/".join(components[:index + 1]), set()).add(test)

        def directory_enabled(path):
            if path in groups:
                # Directory (or sub-directory of or file in it) was directly named.
                return True
            # Check if an ancestor directory was directly name:
            directory = path.rpartition("/")[0]
            while directory:
                if directory in groups and directory + "/" in groups[directory]:
                    return True
                directory = directory.rpartition("/")[0]
            return False

        def file_enabled(path):
            if path in tests:
                # File was directly named.
                return True
            directory, _, name = path.rpartition("/")
            if directory in groups:
                # Loop over all enabled items in the same directory as 'path':
                for item in groups[directory]:
                    if item != directory:
                        local, slash, _ = item[len(directory + "/"):].partition("/")
                        if local > name and (slash or "/" not in directory):
                            # A file in a later sub-directory is enabled; this
                            # means 'path' is a dependency of the other file.
                            return "dependency"
            # Check if the file's directory or an ancestor directory thereof was
            # directly name:
            while directory:
                if directory in groups and directory + "/" in groups[directory]:
                    return True
                directory = directory.rpartition("/")[0]
            return False
    else:
        def directory_enabled(path):
            return True
        def file_enabled(path):
            return True

    def pause():
        testing.pause("Testing paused.  Press ENTER to continue: ")

    pause_before = set(arguments.pause_before or [])
    pause_after = set(arguments.pause_after or [])

    def run_group(group_name):
        try:
            instance.start()
            instance.mailbox = mailbox

            def run_tests(directory):
                has_failed = False

                if directory in pause_before:
                    pause()

                for name in sorted(os.listdir(os.path.join("testing/tests", directory))):
                    if not re.match("\d{3}-", name):
                        continue

                    path = os.path.join(directory, name)

                    if os.path.isdir(os.path.join("testing/tests", path)):
                        if not directory_enabled(path):
                            logger.debug("Skipping: %s/" % path)
                        elif has_failed:
                            logger.info("Skipping: %s/ (failed dependency)" % path)
                        else:
                            run_tests(path)
                        continue
                    elif not re.search("\\.py$", name):
                        continue

                    enabled = file_enabled(path)
                    if not enabled:
                        logger.debug("Skipping: %s" % path)
                        continue

                    if path in pause_before:
                        pause()
                    if enabled is True:
                        mode = ""
                    else:
                        mode = " (%s)" % enabled
                    logger.info("Running: %s%s" % (path, mode))
                    counters.tests_run += 1
                    try:
                        execfile(os.path.join("testing/tests", path),
                                 { "testing": testing,
                                   "logger": logger,
                                   "instance": instance,
                                   "frontend": frontend,
                                   "repository": repository,
                                   "mailbox": mailbox })
                    except testing.TestFailure as failure:
                        counters.tests_failed += 1
                        if failure.message:
                            logger.error(failure.message)
                        while True:
                            mail = mailbox.pop(
                                accept=testing.mailbox.to_recipient("admin@example.org"),
                                timeout=1)
                            if not mail:
                                break
                            logger.error("Administrator message: %s\n  %s"
                                         % (mail.header("Subject"),
                                            "\n  ".join(mail.lines)))
                        if arguments.pause_on_failure:
                            pause()
                        if "/" in directory:
                            has_failed = True
                        else:
                            return
                    else:
                        if path in pause_after:
                            pause()

                if directory in pause_after:
                    pause()

            run_tests(group_name)
        except KeyboardInterrupt:
            logger.error("Testing aborted.")
            return False
        except testing.Error as error:
            if error.message:
                logger.exception(error.message)
            if arguments.pause_on_failure:
                pause()
            return False
        except Exception:
            logger.exception("Unexpected exception!")
            if arguments.pause_on_failure:
                pause()
            return False
        else:
            return True

    for group_name in sorted(os.listdir("testing/tests")):
        if not re.match("\d{3}-", group_name):
            continue

        if not directory_enabled(group_name):
            logger.debug("Skipping: %s/" % group_name)
            continue

        repository = testing.repository.Repository(
            arguments.vbox_host,
            arguments.git_daemon_port,
            tested_commit,
            arguments.vm_hostname)
        mailbox = testing.mailbox.Mailbox({ "username": "smtp_username",
                                            "password": "SmTp_PaSsWoRd" })

        with repository:
            with mailbox:
                if not repository.export():
                    return

                with instance:
                    if run_group(group_name):
                        instance.finish()

                mailbox.check_empty()

    logger.info("""
Test summary
============
Tests run:       %3d
Tests failed:    %3d
Errors logged:   %3d
Warnings logged: %3d
""" % (counters.tests_run,
       counters.tests_failed,
       counters.errors_logged,
       counters.warnings_logged))

if __name__ == "__main__":
    main()
