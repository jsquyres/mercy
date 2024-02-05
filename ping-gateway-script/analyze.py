#!/usr/bin/env python3
#
# Simple script to gather some ping-the-gateway data on a continual
# basis for some testing of network behavior in Mercy's environment.

import re
import os
import json
import argparse
import datetime

from pprint import pprint, pformat

import pytz
local_tz_name = 'America/Louisville'
local_tz = pytz.timezone(local_tz_name)

def convert_to_dt(item, field):
    val = item[field]
    dt = datetime.datetime.fromisoformat(val)
    dt = dt.astimezone(local_tz)
    item[field] = dt

def save_result(results, ts, message, item):
    if ts not in results:
        results[ts] = list()
    results[ts].append({
        'message' : message,
        'item' : item,
    })

def analyze(dir, file):
    filename = os.path.join(dir, file)
    if not os.path.exists(filename):
        print(f"ERROR: Can't open {filename}")
        exit(1)

    print(f'Analyzing file: {filename}')
    with open(filename) as fp:
        data = json.load(fp)

    # Look for:
    # - IP address change
    # - "ping happy" = false
    # - lease changes
    ip = None
    lease_end = None
    results = dict()
    prev_dhcp_lease_time_left = None

    for item in data:
        convert_to_dt(item, 'start')
        convert_to_dt(item, 'end')
        for dhcp_item in item['dhcp data']:
            convert_to_dt(dhcp_item, 'lease start')
            convert_to_dt(dhcp_item, 'lease end')

        ts = item['start']

        if ip is None:
            ip = item['dhcp data'][0]['ip']
        elif ip != item['dhcp data'][0]['ip']:
            save_result(results, ts, 'IP change', item)
            ip = item['dhcp data'][0]['ip']

        if lease_end is None:
            lease_end = item['dhcp data'][0]['lease end']
        else:
            # See if our DHCP lease ended
            if lease_end != item['dhcp data'][0]['lease end']:
                message = 'DHCP renew'
                if prev_dhcp_lease_time_left:
                    message += f': prev lease time left {prev_dhcp_lease_time_left}'
                save_result(results, ts, message, item)
                lease_end = item['dhcp data'][0]['lease end']
        prev_dhcp_lease_time_left = item['dhcp data'][0]['lease time left']

        if item['ping happy'] == False:
            message = 'Ping unhappy'

            # Extract the 'x.y% packet loss' message
            found = re.search(r'([0-9]+.[0-9]+% packet loss)',
                              item['ping stdout'])
            if found:
                message += f': {found.group(1)}'
            save_result(results, ts, message, item)

    for ts in sorted(results):
        result_items = results[ts]
        first = True
        l = 0
        for ri in result_items:
            if first:
                ts = str(ri['item']['start']) + ': '
                l = len(ts)
            else:
                ts = ' ' * l
            print(f"{ts}{ri['message']}")

#----------------

def doit(dir):
    files = os.listdir(dir)
    for file in sorted(files):
        if file.endswith('.json'):
            analyze(dir, file)

base_dir = '/Users/jsquyres/Desktop/mercy/ping-data-week-of-2024-01-29'
estill_dir = os.path.join(base_dir, 'estill-laptop')
doit(estill_dir)
student_dir = os.path.join(base_dir, 'student-device')
doit(student_dir)
