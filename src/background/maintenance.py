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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(sys.argv[0]), "..")))

import configuration
import dbutils
import gitutils
import background.utils

class Maintenance(background.utils.BackgroundProcess):
    def __init__(self):
        service = configuration.services.MAINTENANCE

        super(Maintenance, self).__init__(service=service)

        hour, minute = service["maintenance_at"]
        self.register_maintenance(hour=hour, minute=minute, callback=self.__maintenance)

    def run(self):
        with dbutils.Database() as db:
            # Do an initial load/update of timezones.
            #
            # The 'timezones' table initially (post-installation) only contains
            # the Universal/UTC timezone; this call adds all the others that the
            # PostgreSQL database server knows about.
            dbutils.loadTimezones(db)

        super(Maintenance, self).run()

    def __maintenance(self):
        with dbutils.Database() as db:
            cursor = db.cursor()

            # Update the UTC offsets of all timezones.
            #
            # The PostgreSQL database server has accurate (DST-adjusted) values,
            # but is very slow to query, so we cache the UTC offsets in our
            # 'timezones' table.  This call updates that cache every night.
            # (This is obviously a no-op most nights, but we don't want to have
            # to care about which nights it isn't.)
            self.debug("updating timezones")
            dbutils.updateTimezones(db)

            if self.terminated:
                return

            # Run a garbage collect in all Git repositories, to keep them neat
            # and tidy.
            cursor.execute("SELECT name FROM repositories")
            for (repository_name,) in cursor:
                self.debug("repository GC: %s" % repository_name)
                try:
                    repository = gitutils.Repository.fromName(db, repository_name)
                    repository.run("gc", "--prune=1 day", "--quiet")
                    repository.stopBatch()
                except Exception:
                    self.exception("repository GC failed: %s" % repository_name)

                if self.terminated:
                    return

def start_service():
    maintenance = Maintenance()
    maintenance.run()

background.utils.call("maintenance", start_service)
