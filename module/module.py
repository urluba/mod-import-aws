#!/usr/bin/python

# -*- coding: utf-8 -*-

# Copyright (C) 2009-2012:
#    Gabes Jean, naparuba@gmail.com
#    Gerhard Lausser, Gerhard.Lausser@consol.de
#    Gregory Starck, g.starck@gmail.com
#    Hartmut Goebel, h.goebel@goebel-consult.de
#
# This file is part of Shinken.
#
# Shinken is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Shinken is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Shinken.  If not, see <http://www.gnu.org/licenses/>.
# This module imports hosts and services configuration from a MySQL Database
# Queries for getting hosts and services are pulled from shinken-specific.cfg configuration file.

import os

# Try to import the Libcloud API
from pprint import pprint
try:
    from libcloud.compute.types import Provider
    from libcloud.compute.providers import get_driver
except ImportError:
    Provider = get_driver = None

from shinken.basemodule import BaseModule
from shinken.log import logger

properties = {
    'daemons': ['arbiter'],
    'type': 'aws_import',
    'external': False,
    'phases': ['configuration'],
}


# called by the plugin manager to get a broker
def get_instance(plugin):
    logger.debug("[AWS Importer Module]: Get AWS importer instance for plugin %s" % plugin.get_name())

    if not Provider:
        raise Exception('Missing module libcloud. Please install it from http://libcloud.apache.org/index.html')

    # Beware : we must have RAW string here, not unicode!
    api_key = str(plugin.api_key.strip())
    secret = str(plugin.secret.strip())
    default_template = getattr(plugin, 'default_template', '')
    ignore_tag = getattr(plugin, 'ignore_tag', None)
    regions = getattr(plugin, 'regions', 'ec2_us_east').split(',')
    poller_tag = getattr(plugin, 'poller_tag', None)
    
    instance = AWS_importer_arbiter(plugin, api_key, secret, default_template, ignore_tag,regions,poller_tag)
    return instance


# Retrieve hosts from AWS API
class AWS_importer_arbiter(BaseModule):
    def __init__(self, mod_conf, api_key, secret, default_template, ignore_tag, regions, poller_tag):
        BaseModule.__init__(self, mod_conf)
        self.api_key = api_key
        self.secret = secret
        self.default_template = default_template
        self.ignore_tag = ignore_tag
        self.regions = regions
        self.poller_tag = poller_tag
        self.cons = []


    # Called by Arbiter to say 'let's prepare yourself guy'
    def init(self):
        logger.debug("[AWS Importer Module]: Try to open a AWS connection")
        for region in self.regions:
            self.cons.append(get_driver(getattr(Provider, "EC2"))(self.api_key, self.secret, region=region.lower()))

        logger.info("[AWS Importer Module]: Connection opened")


    # Main function that is called in the CONFIGURATION phase
    def get_objects(self):
        # Create variables for result
        r = {'hosts' : []}

        # Ok get all!
        nodes = []
        try:
            for conn in self.cons:
                nodes.extend(conn.list_nodes())
        except Exception, exp:
            logger.error("[AWS Importer Module]: Error during the node listing '%s'" % exp)
            raise
        hosts = r['hosts']

        for n in nodes:
            h = {}

            hostgroups = []

            # The templates we will use to really configure the VM
            tags = []
            if self.default_template:
                tags.append(self.default_template)

            tags.append('EC2')

            # Append the instance id to the name since AWS allows for Name to be duplicated across instances
            h['host_name'] = unicode(n.name + "_" + n.id)
            
            logger.info("[AWS Importer Module]: I see %s" %  h['host_name'])

            # Now the network part, try to get some :)
            try:
                h['_EC2_PRIVATE_IP'] = unicode(n.private_ips[0])
            except IndexError:
                h['_EC2_PRIVATE_IP'] = u''
            logger.debug("[AWS Importer Module]: Private IP %s" % h['_EC2_PRIVATE_IP'])

            try:
                h['_EC2_PUBLIC_IP'] = unicode(n.public_ips[0])
            except IndexError:
                h['_EC2_PUBLIC_IP'] = u''
            
            # use private ip
            if h['_EC2_PRIVATE_IP']:
                h['address'] = h['_EC2_PRIVATE_IP']
            else:
                logger.warning("[AWS Importer Module]: host %s has no private IP" % h['host_name'])
                h['address'] = u''
                h['initial_state'] = u'u'
                h['active_checks_enabled'] = u'0'
                h['passive_checks_enabled'] = u'0'

            logger.debug("[AWS Importer Module]: Shinken IP %s" % h['address'])

            # Ok massive macro setup, but if possible in a clean way
            for (k, v) in n.extra.iteritems():

                prop = '_EC2_'+k.upper()

                if isinstance(v, list):
                    try:
                        logger.debug("[AWS Importer Module]: list %s" % k)
                        h[prop] = ','.join(filter(None,v))
                    except TypeError:
                        logger.debug(k + " is not a simple list: " + str(v))
                elif isinstance(v, dict):
                    logger.debug("[AWS Importer Module]: dict %s" % k)
                    h[prop] = ','.join(['%s:%s' % (i, j) for (i,j) in v.iteritems()])
                else:
                    logger.debug("[AWS Importer Module]: %s = %s" % (k, v))
                    h[prop] = unicode(v)

                # Special hooks
                if k == 'tags' and 'aws:cloudformation:stack-name' in v:
                    logger.info( "[AWS Importer Module]: adding %s to group %s" % (h['host_name'], v['aws:cloudformation:stack-name']))
                    hostgroups.append(v['aws:cloudformation:stack-name'])

                if k == 'tags' and 'Role' in v:
                    logger.info( "[AWS Importer Module]: adding %s to group %s" % (h['host_name'], v['Role']))
                    hostgroups.append(v['Role'])

                # We take the "use" tag as a use parameter
                #if k == 'tags' and 'use' in v:
                    #if v['use'] == self.ignore_tag:
                        #break
                    #else:
                        #tags.append(v['use'])
                # Also put as template the instance type
                #if k == 'instancetype':
                    #tags.append(v)
            else:
                # Non running hosts are set down
                if h.get('_EC2_STATUS') != "running":
                    logger.warning("[AWS Importer Module]: %s is down" % h['host_name'] )
                    h['initial_state'] = u'u'
                    h['active_checks_enabled'] = u'0'
                    h['passive_checks_enabled'] = u'0'

                # The tag order is not the good, precise data are on the end, we want them
                # first
                tags.reverse()

                h['use'] = ','.join(tags)
                h['hostgroups'] = ','.join(hostgroups)

                if self.poller_tag == 'availabilityzone':
                    h['poller_tag'] = h['_EC2_AVAILABILITY']
                elif self.poller_tag == 'region':
                    h['poller_tag'] = h['_EC2_AVAILABILITY'][:-1]
                    #h['poller_tag'] = h['_EC2_AVAILABILITY'][:-1].replace('-','_').upper()

		#logger.info("[AWS Importer Module]: %s" % h)
                hosts.append(h)

        # End for n in nodes:

        print "Discovered hosts"
        pprint(hosts)
        logger.info("[AWS Importer Module]: Returning to Arbiter %d hosts" % len(r['hosts']))
        return r
