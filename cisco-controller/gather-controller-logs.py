#!/usr/bin/env python3

# Uses pexpect: https://pypi.org/project/pexpect/
#   sudo yum install python3-pexpect
# or
#   pip3 install pexpect

import argparse
import logging
import pexpect
import sqlite3
import re

from pprint import pprint
from pprint import pformat

################################################################

# Might as well hard-wire these

prompt = 'Cisco Controller'
default_controllers = {
    'CTS2504-1' : { 'name' : 'CT2504-1', 'prompt_name': prompt,
                 'ip' : '192.168.81.253' },
    'CTS2504-2' : { 'name' : 'CT2504-2', 'prompt_name': prompt,
                    'ip' : '192.168.81.252' },
}

default_user      = 'guest'
default_password  = 'password'

default_sqlite_db = 'database.sqlite3'

################################################################

def setup_cli():
    parser = argparse.ArgumentParser(description='Analyze Cisco wireless controller stats')
    parser.add_argument('--user',
                        default=default_user,
                        help='Username for controller login')
    parser.add_argument('--password',
                        default=default_password,
                        help='Password for controller login')

    parser.add_argument('--db',
                        default=default_sqlite_db,
                        help='SQLite3 database filename to store results')

    parser.add_argument('--debug',
                        action='store_true',
                        help='Enable extra output for debugging')
    parser.add_argument('--fake',
                        action='store_true',
                        help='Do not actually try to talk to controllers')

    args = parser.parse_args()

    return args

#---------------------------------------------------------------

def setup_logging(args):
    log = logging.getLogger('GithubPRwaiter')
    level = logging.INFO
    if args.debug:
        level = logging.DEBUG
    log.setLevel(level)

    ch = logging.StreamHandler()
    ch.setLevel(level)

    format = '%(asctime)s %(levelname)s: %(message)s'
    formatter = logging.Formatter(format)

    ch.setFormatter(formatter)

    log.addHandler(ch)

    return log

################################################################

def connect_to_controllers(controllers, args, log):
    connections = list()
    for _, data in controllers.items():
        log.info("Connecting to controller '{name}' at {user}@{ip}..."
                 .format(name=data['name'], user=args.user, ip=data['ip']))

        cmd = ('ssh {user}@{ip}'
               .format(user=args.user, ip=data['ip']))
        child = pexpect.spawn(cmd)
        log.debug("Waiting for User...")
        child.expect("User: ")
        log.debug("Sending username")
        child.sendline(args.user)
        log.debug("Waiting for password...")
        child.expect("Password:")
        log.debug("Sending password")
        child.sendline(args.password)

        prompt = "\({name}\) >".format(name=data['prompt_name'])
        log.debug("Waiting for '{prompt}'...".format(prompt=prompt))
        child.expect(prompt, timeout=3)

        c = data.copy()
        c['expect'] = child
        c['prompt'] = prompt

        connections.append(c)

    return connections

#---------------------------------------------------------------

def disconnect_from_controllers(controllers, log):
    for _, data in controllers.items():
        log.info("Disconnecting from controller '{name}' at {ip}..."
                 .format(name=data['name'], ip=data['ip']))

        e = data['expect']
        e.sendline('logout')
        e.expect(pexpect.EOF)

#---------------------------------------------------------------

def gather_wlans(controller, log):
    # show wlan summary
    '''
(Cisco Controller) >show wlan summary
Number of WLANs.................................. 1

WLAN ID  WLAN Profile Name / SSID               Status    Interface Name
-------  -------------------------------------  --------  --------------------
1        apsso / apsso                          Disabled  management

'''
    log.info("Querying WLAN IDs on controller '{name}' ({ip})..."
             .format(name=controller['name'], ip=controller['ip']))
    wlans = dict()

    e = controller['expect']
    e.sendline('show wlan summary')
    e.expect(controller['prompt'], timeout=5)

    lines  = e.before.splitlines()
    inside = False
    for line in lines:
        line = line.decode('utf-8').strip()

        if len(line) == 0:
            # Skip blank lines
            continue
        elif line.startswith('----'):
            log.debug("Found beginning of WLAN IDs")
            inside = True
            continue
        elif not inside:
            log.debug("Not a WLAN ID: {l}".format(l=line))
            continue

        # If we're here, then we're inside the listing of WLAN IDs.
        # Parse out the individual fields.
        log.debug("Parsing WLAN ID line: {l}".format(l=line))

        # Columns aren't sexy, but the Cisco interface is fixed
        # columns, so this is guaranteed to work.
        # NOTE: These dictionary key names must match their
        # corresponding index SQL table field names!
        parts = line[9:45].split("/")
        wid = {
            'wlan_id'         : int(line[0:6]),
            'profile_name'    : parts[0].strip(),
            'ssid'            : parts[1].strip(),
            'enabled'         : line[48:55].strip(),
            'interface'       : line[58:77].strip(),
        }
        log.debug("Got the following WLAN ID: {wid}"
                  .format(wid=pformat(wid)))

        wlans[wid['wlan_id']] = wid

    return wlans

#---------------------------------------------------------------

def gather_aps(controller, aps, log):
    # show ap summary
    '''
(Cisco Controller) >show ap summary
Number of APs.................................... 2
Global AP username.............................. user
Global AP Dot1x username........................ Not Configured
Number of APs.................................... 2
Global AP username.............................. user
Global AP Dot1x username........................ Not Configured


AP Name            Slots AP Model             Ethernet MAC      Location         Country IP Address       Clients
------------------  -----  --------------------  -----------------  ----------------  -------  ---------------  -------
AP1140               2   AIR-LAP1142N-A-K9    f0:f7:55:75:f3:29 default location    US   192.168.0.0         0

Access Points using IPv6 transport:

AP Name            Slots AP Model             Ethernet MAC      Location         Country IPv6 Address     Clients
------------------ ----- -------------------- ----------------- ---------------- ------- ---------------  -------
AP1040              2   AIR-LAP1042N-A-K9    00:40:96:b9:4b:89 default location  US     2001:DB8:0:1::1     0


AP Name             Slots  AP Model              Ethernet MAC       Location          Country  IP Address       Clients
------------------  -----  --------------------  -----------------  ----------------  -------  ---------------  -------
AP-Auditorium-r      2     AIR-CAP3602E-A-K9     a8:0c:0d:9e:68:35  2nd floor contro  US       192.168.81.241   0
AP-Business          2     AIR-CAP1602I-A-K9     54:a2:74:e2:a6:b5  Business Office   US       192.168.81.239   6
AP-MediaCenter1      3     AIR-CAP3602I-A-K9     d4:8c:b5:12:c4:88  Media Center - W  US       192.168.81.203   22
AP108                2     AIR-CAP3602I-A-K9     d8:67:d9:bd:0c:e7            RM 108  US       192.168.81.205   0
AP104                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:1e            Rm 104  US       192.168.81.211   2
AP106                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:18            Rm 106  US       192.168.81.212   3
AP-Gym               3     AIR-CAP3602I-A-K9     a8:9d:21:86:59:19       Gym Ceiling  US       192.168.81.236   14
AP110                2     AIR-CAP3602I-A-K9     d4:8c:b5:0e:2e:b3            Rm 110  US       192.168.81.206   0
AP102                2     AIR-CAP3602I-A-K9     d4:8c:b5:0e:2d:cc            RM 102  US       192.168.81.201   1
AP111                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:30            Rm 111  US       192.168.81.214   1
APSTEM               2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:ae  STEM Innovation   US       192.168.81.215   7
AP-Athletic          2     AIR-CAP1602I-A-K9     78:ba:f9:e6:c6:70  Hallway - Outsid  US       192.168.81.235   2
AP-MediaCenter2      3     AIR-CAP3602I-A-K9     d4:8c:b5:12:c2:ad  Media Center - F  US       192.168.81.204   17
AP-SmallGym          2     AIR-CAP1602I-A-K9     54:a2:74:e2:a7:1c  In Auxiliary Gym  US       192.168.81.238   11

--More-- or (q)uit
AP113                2     AIR-CAP3602I-A-K9     d4:8c:b5:0e:2d:77            Rm 113  US       192.168.81.208   0
AP208                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:2e            Rm 208  US       192.168.81.116   1
AP204                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:68            Rm 204  US       192.168.81.152   0
AP211                2     AIR-CAP3602I-A-K9     d4:8c:b5:0e:2e:e4            Rm 211  US       192.168.81.120   2
AP206                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:41            Rm 206  US       192.168.81.153   0
AP216                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:5b          Room 216  US       192.168.81.158   1
AP210                2     AIR-CAP3602I-A-K9     f4:0f:1b:1a:0e:34          Room 210  US       192.168.81.155   0
AP-Dance             2     AIR-CAP1602I-A-K9     b0:aa:77:26:15:9f   Dance Classroom  US       192.168.81.242   0
AP-President         2     AIR-CAP1602I-A-K9     78:ba:f9:e6:c6:43  2nd floor confer  US       192.168.81.240   1

'''
    log.info("Querying APs on controller '{name}' ({ip})..."
             .format(name=controller['name'], ip=controller['ip']))

    e = controller['expect']
    e.sendline('show ap summary')

    # The list may be paginated; make sure to handle that.
    lines = list()
    while True:
        i = e.expect(['--More-- or \(q\)uit', controller['prompt']],
                     timeout=5)
        lines.extend(e.before.splitlines())

        if i == 0:
            log.debug("Got 'More' -- requesting more...")
            e.sendline('')
        elif i == 1:
            log.debug("Got end of AP list")
            break

    # Ok, we have all the lines now
    inside  = False
    for line in lines:
        line = line.decode('utf-8')

        # NOTE: The output shows *2* tables.  We only want to read the first one.

        if len(line.strip()) == 0:
            # Skip blank lines
            continue
        elif line.startswith('----'):
            log.debug("Found beginning of first AP table")
            inside = True
            continue
        elif not inside:
            log.debug("Not an AP: {l}".format(l=line))
            continue

        # If we're here, we've passed the initial "AP Name..." header
        # line and we're not a blank line. Parse out the individual
        # fields.
        log.debug("Parsing AP line: {l}".format(l=line))

        # Columns aren't sexy, but the Cisco interface is fixed
        # columns, so this is guaranteed to work.
        # NOTE: These dictionary key names must match their
        # corresponding index SQL table field names!
        ap = {
            'name'     : line[0:17].strip(),
            'slots'    : int(line[20:24]),
            'ap_model' : line[27:46].strip(),
            'mac'      : line[49:65].strip(),
            'location' : line[68:83].strip(),
            'country'  : line[86:92].strip(),

            # These fields are not in the index SQL table (and that's
            # ok), so they can be named whatever we want.
            'ip'       : line[95:109].strip(),
            'clients'  : int(line[112:118]),
        }
        log.debug("Got the following AP: {ap}"
                  .format(ap=pformat(ap)))

        aps[ap['name']] = ap

#---------------------------------------------------------------

def gather_clients(controller, wlans, aps, clients, log):
    # show client ap <-- shows clients on a specific AP
    '''
(Cisco Controller) >show client ap 802.11b AP1
MAC Address       AP Id  Status        WLAN Id   Authenticated
----------------- ------ ------------- --------- -------------
xx:xx:xx:xx:xx:xx     1  Associated    1         No
'''

    # show client summary <--> shows all clients and their AP name
    # (probably atomic)
    '''
(Cisco Controller) > show client summary
                                                       RLAN/
MAC Address       AP Name           Slot Status        WLAN  Auth Protocol         Port Wired PMIPV6 Role
----------------- ----------------- ---- ------------- ----- ---- ---------------- ---- ----- ------ ----------------
00:56:cd:8b:ea:61 AP-Gym             2   Associated     3    Yes  802.11ac(5 GHz)  13   N/A   No     Local
00:db:70:5b:69:89 AP-Gym             2   Associated     3    Yes  802.11ac(5 GHz)  13   N/A   No     Local
00:db:70:78:81:eb AP-Gym             2   Associated     3    Yes  802.11ac(5 GHz)  13   N/A   No     Local
00:db:70:c7:e0:6b AP-Gym             2   Associated     3    Yes  802.11ac(5 GHz)  13   N/A   No     Local
08:f6:9c:06:c2:b7 AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:06:ec:ff AP-MediaCenter1    0   Associated     3    Yes  802.11n(2.4 GHz) 13   N/A   No     Local
08:f6:9c:07:55:72 AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:07:70:89 AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:08:03:78 AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:0a:ba:d3 AP-MediaCenter1    0   Associated     3    Yes  802.11n(2.4 GHz) 13   N/A   No     Local


Would you like to display more entries? (y/n) y

08:f6:9c:0a:db:71 AP-MediaCenter1    0   Associated     3    Yes  802.11n(2.4 GHz) 13   N/A   No     Local
08:f6:9c:0c:36:bf AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:0c:41:23 AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:0e:23:ad AP-MediaCenter1    0   Associated     3    Yes  802.11n(2.4 GHz) 13   N/A   No     Local
08:f6:9c:0f:82:dd AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:15:35:d8 AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
08:f6:9c:15:41:5d AP-MediaCenter1    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
1c:e6:2b:e5:b1:e2 AP-SmallGym        1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
1c:e6:2b:e9:82:5e AP106              1   Associated     1    Yes  802.11n(5 GHz)   13   N/A   No     Local
20:3c:ae:9b:47:4e AP-Business        0   Associated     3    Yes  802.11n(2.4 GHz) 13   N/A   No     Local
2c:b4:3a:20:db:8a AP-MediaCenter2    1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
34:e6:ad:09:5f:a1 AP-Athletic        1   Associated     1    Yes  802.11n(5 GHz)   13   N/A   No     Local
38:89:2c:2c:e3:ca AP-SmallGym        1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
40:cb:c0:15:5c:eb AP-SmallGym        1   Associated     3    Yes  802.11n(5 GHz)   13   N/A   No     Local
48:a1:95:a7:1a:b4 AP-Gym             2   Associated     3    Yes  802.11ac(5 GHz)  13   N/A   No     Local

'''
    log.info("Querying clients on controller '{name}' ({ip})..."
             .format(name=controller['name'], ip=controller['ip']))
    log.debug("All the APs we found:\n{aps}".format(aps=pformat(aps)))

    e = controller['expect']
    e.sendline('show client summary')

    # The list may be paginated; make sure to handle that.
    lines = list()
    while True:
        i = e.expect(['Would you like to display more entries\? \(y/n\) ', controller['prompt']],
                     timeout=5)
        lines.extend(e.before.splitlines())

        if i == 0:
            log.debug("Got 'More' -- requesting more...")
            e.send('y')
        elif i == 1:
            log.debug("Got end of client list")
            break

    protocol_re = re.compile('(.*)\((.+)\)')

    # Ok, we have all the lines now
    inside  = False
    for line in lines:
        line = line.decode('utf-8')

        if len(line.strip()) < 2:
            # Skip blank (and nearly blank) lines
            continue
        elif line.startswith('----'):
            log.debug("Found beginning of client table")
            inside = True
            continue
        elif not inside:
            log.debug("Not a client: {l}".format(l=line))
            continue

        # If we're here, we've passed the initial "AP Name..." header
        # line and we're not a blank line. Parse out the individual
        # fields.
        log.debug("Parsing AP line: {l}".format(l=line))

        # Columns aren't sexy, but the Cisco interface is fixed
        # columns, so this is guaranteed to work.
        protocol_parts = line[66:82].strip()
        log.debug("Protocol parts: {p}".format(p=protocol_parts))
        parts = protocol_re.match(protocol_parts)
        if parts:
            protocol  = parts.group(1)
            frequency = float(parts.group(2))
        else:
            protocol  = protocol_parts
            frequency = ''

        ap_name = line[18:36].strip()
        wlan_id = int(line[55:59])
        log.debug("AP name: {n}".format(n=ap_name))
        # NOTE: These dictionary key names must match their
        # corresponding index SQL table field names!
        client = {
            'mac'       : line[0:16].strip(),

            # These fields are not in the index SQL table (and that's
            # ok), so they can be named whatever we want.
            'ap_name'   : aps[ap_name],
            'slot'      : int(line[37:39]),
            'status'    : line[41:53].strip(),
            'wlan_id'   : wlans[wlan_id],
            'auth'      : line[61:64].strip(),
            'protocol'  : protocol,
            'frequency' : frequency,
            'port'      : int(line[83:86]),
            'wired'     : line[88:92],
            'pmipv6'    : line[94:99],
            'role'      : line[101:116],
        }
        log.debug("Got the following client: {client}"
                  .format(client=pformat(client)))

        clients[client['mac']] = client

    return clients

################################################################

def gather_data_fake(args, log):
    wlans       = {
        1: { 'wlan_id' : 1, 'profile_name' : 'fake1', 'ssid' : 'fake1',
             'enabled' : 'Enabled', 'interface' : 'management' },
        2: { 'wlan_id' : 2, 'profile_name' : 'fake2', 'ssid' : 'fake2',
             'enabled' : 'Enabled', 'interface' : 'management' },
    }
    aps         = {
        'fake_ap1' : { 'name' : 'fake_ap1', 'slots' : 2,
                       'ap_model' : 'shiny',
                       'mac' : '11:22:33:44:55:66', 'location' : 'Hallway',
                       'country' : 'US', 'ip' : '192.168.1.100',
                       'clients' : 13 },
        'fake_ap2' : { 'name' : 'fake_ap2', 'slots' : 2,
                       'ap_model' : 'dull',
                       'mac' : '11:22:33:44:55:77', 'location' : 'Room',
                       'country' : 'US', 'ip' : '192.168.1.101',
                       'clients' : 34 },
    }
    clients     = {
        '22:33:44:55:66:77' : { 'mac' : '22:33:44:55:66:77',
                                'ap_name' : 'fake_ap1', 'slot' : 2,
                                'status' : 'Associated', 'wlan_id' : 2,
                                'auth' : 'Yes',
                                'protocol' : 'ac', 'frequency' : 5,
                                'port' : 13,
                                'wired' : 'N/A', 'pmipv6' : 'No',
                                'role' : 'Local' },
        '22:33:44:55:66:88' : { 'mac' : '22:33:44:55:66:88',
                                'ap_name' : 'fake_ap2', 'slot' : 2,
                                'status' : 'Associated', 'wlan_id' : 1,
                                'auth' : 'Yes',
                                'protocol' : 'n', 'frequency' : 2.4,
                                'port' : 13,
                                'wired' : 'N/A', 'pmipv6' : 'No',
                                'role' : 'Local' },
    }

    return default_controllers, wlans, aps, clients

#---------------------------------------------------------------

def gather_data_real(args, log):
    controllers = connect_to_controllers(default_controllers, args, log)

    # We already know that the WLAN IDs are the same between all of
    # our controllers.  So just obtain them from one of the
    # controllers.
    wlans = gather_wlans(controllers[1], log)

    # For each controller, download a bunch of data.
    aps     = dict()
    clients = dict()
    for _, controller in controllers.items():
        gather_aps(controller=controller, aps=aps, log=log)
        gather_clients(controller=controller, wlans=wlans,
                       aps=aps, clients=clients, log=log)

    disconnect_from_controllers(controllers, log)

    return controllers, wlans, aps, clients

#---------------------------------------------------------------

def gather_data(args, log):
    if args.fake:
        controllers, wlans, aps, clients = gather_data_fake(args, log)
    else:
        controllers, wlans, aps, clients = gather_real_fake(args, log)

    log.debug("=================================================")
    log.debug("Gathered Controllers")
    log.debug(pformat(controllers))
    log.debug("=================================================")
    log.debug("Gathered WLANs")
    log.debug(pformat(wlans))
    log.debug("=================================================")
    log.debug("Gathered APs")
    log.debug(pformat(aps))
    log.debug("=================================================")
    log.debug("Gathered Clients")
    log.debug(pformat(clients))

    return controllers, wlans, aps, clients

################################################################

def db_connect(filename, log):
    # Use the sqlite3.Row factory so that we can get field names
    log.debug("Connecting to database: {db}".format(db=filename))
    conn             = sqlite3.connect(filename)
    conn.row_factory = sqlite3.Row
    cur              = conn.cursor()

    return cur

#---------------------------------------------------------------

def db_disconnect(cur):
    cur.connection.close()

#===============================================================

# SQL table schemas
def db_get_schemas():
    schemas = {
        'wlans' : '''
CREATE TABLE wlans (
       id integer primary key autoincrement,
       timestamp datetime default current_timestamp,

       wlan_id integer,
       profile_name char(32),
       ssid char(32),
       enabled integer,
       interface char(20)
)
''',

        'controllers' : '''
CREATE TABLE controllers (
       id integer primary key autoincrement,
       timestamp datetime default current_timestamp,

       name char(30),
       ip char(16)
)
''',

        'aps' : '''
CREATE TABLE aps (
       id integer primary key autoincrement,
       timestamp datetime default current_timestamp,

       name char(20),
       ap_model char(20),
       slots integer,
       mac char(20),
       location char(16),
       country char(8)
)
''',

        'clients' : '''
CREATE TABLE clients (
       id integer primary key autoincrement,
       timestamp datetime default current_timestamp,

       mac char(18)
)
''',

        'ap_sightings' : '''
CREATE TABLE ap_sightings (
       id integer primary key autoincrement,
       timestamp datetime default current_timestamp,

       ap_index integer,

       ip char(16),
       num_clients integer
)
''',

        'client_sightings' : '''
CREATE TABLE client_sightings (
       id integer primary key autoincrement,
       timestamp datetime default current_timestamp,

       client_index integer,
       ap_index integer,
       wlan_index integer,

       protocol_802dot11 char(4),
       frequency_ghz float
)
'''
    }

    return schemas

#===============================================================

# Go through all the schemas.  If the table does not already exist in
# the database, create it.
def db_create_tables(cur, schemas, log):
    for name, schema in schemas.items():
        sql = ("SELECT name FROM sqlite_master WHERE type='table' AND name='{name}'"
                 .format(name=name))
        log.debug("Executing SQL table check: {sql}".format(sql=sql))
        cur.execute(sql)
        result = cur.fetchone()

        if result:
            log.debug("Table {name} exists in database; no need to create it"
                      .format(name=name))
        else:
            log.debug("Table {name} does not exist in database; creating it"
                      .format(name=name))
            log.debug("Executing SQL table creation: {table}"
                      .format(table=name))
            cur.execute(schema)

    cur.connection.commit()

#===============================================================

# Read any database table, return it in a dictionary indexed by the
# "id" field.
def db_table_read(cur, name, log):
    # First, query to get all the field names in this table.
    # a) we know we're using sqlite, so we use an sqlite-specific
    # method
    # b) if we knew for a fact that the table would have data in it,
    # we could just query .keys() off any row result to get the field
    # names.  But the tables may not have data in them, so we can't
    # count on this.
    sql         = 'PRAGMA table_info({name})'.format(name=name)
    log.debug("Executing SQL: {sql}".format(sql=sql))
    result      = cur.execute(sql)
    field_names = list()
    for row in result.fetchall():
        field_names.append(row[1])

    # Now that we have the field names, read in the entire table
    sql    = ("SELECT * FROM {table}".format(table=name))
    log.debug("Executing SQL: {sql}".format(sql=sql))
    result = cur.execute(sql)
    rows   = dict()
    for row in result.fetchall():
        data = dict()
        for fname in field_names:
            data[fname] = row[fname]

        rows[data['id']] = data

    table = {
        'field_names' : field_names,
        'rows'        : rows,
    }

    return table

#---------------------------------------------------------------

# Read all the tables, storing each table in a master dictionary
def db_read_tables(cur, schemas, log):
    db = dict()

    for table, _ in schemas.items():
        log.debug("Reading database table: {name}".format(name=table))
        db[table] = db_table_read(cur, table, log)

    log.debug("=================================================")
    log.debug("Database tables")
    log.debug(pformat(db))
    log.debug("=================================================")

    return db

#===============================================================

def db_insert(cur, table_name, field_names, values, log):
    sql   = 'INSERT INTO {name} ('.format(name=table_name)
    sql2  = ') VALUES ('
    first = True
    for fname in field_names:
        # If we don't have this field in the values, skip it
        if fname not in values:
            continue

        if not first:
            sql  += ','
            sql2 += ','
        sql  += fname
        sql2 += '"{value}"'.format(value=values[fname])

        first = False

    sql += sql2 + ')'

    log.debug("Executing SQL insert: {sql}".format(sql=sql))
    cur.execute(sql)
    cur.connection.commit()

    log.info("Added to {name} index table: {values}"
             .format(name=table_name, values=values))

#---------------------------------------------------------------

def compare_index_table(cur, db, table_name, gathered_data, log):
    updated           = False
    table             = db[table_name]
    table_field_names = table['field_names']

    # For every row in the data, see if we can find an exact match in
    # the database.  If not, insert it.
    for _, gathered_row in gathered_data.items():
        matched = False

        for _, table_row in table['rows'].items():
            # If we have any rows, assume that we match, unless proven
            # otherwise (below).
            log.debug("Comparing: {a} to {b}"
                      .format(a=gathered_row, b=table_row))

            matched = True
            for fname in table_field_names:
                # If the DB field is not in the gathered row, skip it
                # / don't consider it when checking for a match (i.e.,
                # probably the DB index or timestamp)
                if fname not in gathered_row:
                    continue

                log.debug("  ==> Comparing {field}: {a} vs. {b}"
                          .format(field=fname,
                                  a=gathered_row[fname],
                                  b=table_row[fname]))
                if gathered_row[fname] != table_row[fname]:
                    matched = False
                    break

            if matched:
                break

        if matched:
            log.debug("This index item is already in the database: {foo}"
                     .format(foo=gathered_row))
        else:
            log.debug("Need to insert this index item into the database: {foo}"
                     .format(foo=gathered_row))
            db_insert(cur=cur, table_name=table_name,
                      field_names=table_field_names, values=gathered_row,
                      log=log)
            updated = True

    return updated

#---------------------------------------------------------------

def db_update_index_tables(cur, db, controllers, wlans, aps, clients, log):
    # Update the index tables
    updated = False
    updated |= compare_index_table(cur=cur, db=db, table_name='wlans',
                                   gathered_data=wlans, log=log)
    updated |= compare_index_table(cur=cur, db=db, table_name='controllers',
                                   gathered_data=controllers, log=log)
    updated |= compare_index_table(cur=cur, db=db, table_name='aps',
                                   gathered_data=aps, log=log)
    updated |= compare_index_table(cur=cur, db=db, table_name='clients',
                                   gathered_data=clients, log=log)

    return updated

################################################################

def compare_ap(gathered_ap, db_ap):
    if (gathered_ap['name']     == db_ap['name'] and
        gathered_ap['ap_model'] == db_ap['ap_model'] and
        gathered_ap['slots']    == db_ap['slots'] and
        gathered_ap['mac']      == db_ap['mac']):
        return True
    return False

def compare_wlan(gathered_wlan, db_wlan):
    if (gathered_wlan['wlan_id'] == db_wlan['wlan_id'] and
        gathered_wlan['ssid']    == db_wlan['ssid']):
        return True
    return False

def compare_client(gathered_client, db_client):
    if (gathered_client['mac'] == db_client['mac']):
        return True
    return False

#---------------------------------------------------------------

def correlate(db, aps, wlans, clients, log):
    log.debug("Correlating gathered data to database index values...")

    def _correlate(gathered_data, db_table, field_name, compare_fn, log):
        for _, gathered_row in gathered_data.items():
            gathered_row[field_name] = None
            for _, db_row in db_table['rows'].items():
                if compare_fn(gathered_row, db_row):
                    gathered_row[field_name] = db_row['id']
                    break

    # Correlate AP, WLAN, and clients to their database index values.
    # This is done through multiple layers of indirection in the above
    # embedded function and passing function pointers for the actual
    # comparisons.  Not for the meek!
    _correlate(aps, db['aps'], 'db_ap_index', compare_ap, log)
    _correlate(wlans, db['wlans'], 'db_wlan_index', compare_wlan, log)
    _correlate(clients, db['clients'], 'db_client_index', compare_client, log)

    # Clients also reference WLANs and APs, so get their database
    # indexes, too.  We nicely setup the gathered WLAN/AP data indexed
    # on how the client sees them, so finding these DB indexes is now
    # trivial.
    for _, client in clients.items():
        wlan_id = int(client['wlan_id'])
        client['db_wlan_index'] = wlans[wlan_id]['db_wlan_index']

        ap_name = client['ap_name']
        client['db_ap_index'] = aps[ap_name]['db_ap_index']

    log.debug(pformat(clients))

################################################################

# I could probably write this more generally, but the "index" fields
# make this propsect a little wonky.  So just leave all the fields /
# values hard-coded.
def write_db_ap_sightings(cur, db, gathered_aps, log):
    for _, gathered_ap in gathered_aps.items():
        sql = ('INSERT INTO ap_sightings (ap_index,ip,num_clients) VALUES ({ap_index},"{ip}",{num_clients})'
               .format(ap_index=gathered_ap['db_ap_index'],
                       ip=gathered_ap['ip'],
                       num_clients=gathered_ap['clients']))
        log.debug("About to insert AP sighting: {sql}".format(sql=sql))
        cur.execute(sql)
        cur.connection.commit()

    log.info("Wrote {num} new AP sightings"
             .format(num=len(gathered_aps)))

# I could probably write this more generally, but the "index" fields
# make this propsect a little wonky.  So just leave all the fields /
# values hard-coded.
def write_db_client_sightings(cur, db, gathered_clients, log):
    for _, gathered_client in gathered_clients.items():
        sql = ('INSERT INTO client_sightings (client_index,ap_index,wlan_index,protocol_802dot11,frequency_ghz) VALUES ({client_index},{ap_index},{wlan_index},"{protocol}",{frequency})'
               .format(client_index=gathered_client['db_client_index'],
                       ap_index=gathered_client['db_ap_index'],
                       wlan_index=gathered_client['db_wlan_index'],
                       protocol=gathered_client['protocol'],
                       frequency=gathered_client['frequency']))
        log.debug("About to insert client sighting: {sql}".format(sql=sql))
        cur.execute(sql)
        cur.connection.commit()

    log.info("Wrote {num} new client sightings"
             .format(num=len(gathered_clients)))

################################################################

def main():
    args = setup_cli()
    log  = setup_logging(args)

    # Go gather the data
    controllers, wlans, aps, clients = gather_data(args=args, log=log)

    # Connect to the database
    cur = db_connect(filename=args.db, log=log)

    # Create DB tables if they don't exist
    schemas = db_get_schemas()
    db_create_tables(cur=cur, schemas=schemas, log=log)

    # Read all the existing database tables
    db = db_read_tables(cur=cur, schemas=schemas, log=log)

    # Update the index tables with the data we gathered
    updated = db_update_index_tables(cur=cur, db=db,
                                     controllers=controllers,
                                     wlans=wlans,
                                     aps=aps,
                                     clients=clients,
                                     log=log)

    # Once the index tables are updates -- although this is a bit
    # wasteful / inefficient -- read in the database again.  It's the
    # simplest way of getting all the data that we may have just
    # inserted into the index tables / refreshing our data structures.
    if updated:
        log.debug("Database has changed -- re-reading tables")
        db = db_read_tables(cur=cur, schemas=schemas, log=log)

    # Correlate gathered APs, WLANs, and clients to their database
    # indexes
    correlate(db=db, aps=aps, wlans=wlans, clients=clients, log=log)

    # Write all new sightings of APs and clients
    write_db_ap_sightings(cur=cur, db=db, gathered_aps=aps, log=log)
    write_db_client_sightings(cur=cur, db=db, gathered_clients=clients,
                              log=log)

    # Close out the database
    db_disconnect(cur)

if __name__ == "__main__":
    main()