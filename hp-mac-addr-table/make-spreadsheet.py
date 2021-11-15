#!/usr/bin/env python3
#
# This is a very short/simple single-purpose script.
#
# It takes the output from the "show mac-address" table on an HP
# switch and turns it into a CSV, skipping all entries that are either
# VLAN 1 (infrastructure) or port 48 (uplink).

import csv
import os
import argparse
import re

parser = argparse.ArgumentParser(description='Mercy HP switch MAC address table analyzer')
parser.add_argument("--input", required=True, help='Filename to read')
parser.add_argument("--output", help='CSV filename to write (default to input filename with .csv suffix)')
args = parser.parse_args()

if not os.path.exists(args.input):
    print(f"ERROR: file does not exist: {filename}")
    exit(1)

if args.output == None:
    if args.input.endswith(".txt"):
        args.output = args.input[:-4] + '.csv'
    else:
        output = args.input + '.csv'

with open(args.input) as fp:
    lines = fp.readlines()

def _reformat(mac):
    # It will come in as xxxxxx-xxxxxx
    # Reformat it to xx:xx:xx:xx:xx:xx
    out = (f'{mac[0]}{mac[1]}:{mac[2]}{mac[3]}:{mac[4]}{mac[5]}:' +
           f'{mac[7]}{mac[8]}:{mac[9]}{mac[10]}:{mac[11]}{mac[12]}')
    return out

ports = dict()
for line in lines:
    line = line.strip()

    # Skip blank lines
    if len(line) == 0:
        continue

    print(line)
    match = re.search('^([a-f0-9\-]+)\s+(\d+)\s+(\d+)$', line)
    mac = _reformat(match.group(1))
    port = int(match.group(2))
    vlan = int(match.group(3))

    # Ignore port 48, the uplink
    if port == 48:
        continue
    # Ignore VLAN 1, the infrastructure VLAN
    if vlan == 1:
        continue

    if port not in ports:
        ports[port] = list()
    ports[port].append({
        'MAC' : mac,
        'VLAN' : vlan,
    })

# Now that we have all the data we care about in a reasonable data
# structure, output it as a CSV.
print(f"Writing to: {args.output}")
with open(args.output, 'w') as fp:
    fields = ['Port', 'MAC', 'VLAN', 'Device']
    writer = csv.DictWriter(fp, fieldnames=fields)
    writer.writeheader()

    for port in sorted(ports):
        for item in ports[port]:
            item = {
                'Port' : port,
                'MAC' : item['MAC'],
                'VLAN' : item['VLAN'],
            }
            writer.writerow(item)
