"""Bounded proof for active Zeroconf inside a disconnected dummy namespace.

This is a fixture readback only. It never turns sockets or interfaces off and
does not classify arbitrary listeners as inactive or grant product authority.
"""
import fcntl
import ipaddress
import os
from pathlib import Path
import re
import socket
import struct


def _ipv6_proc(raw):
    if re.fullmatch('[0-9A-Fa-f]{32}', raw) is None:
        raise ValueError('invalid_ipv6_socket_address')
    return ipaddress.IPv6Address(b''.join(bytes.fromhex(raw[i:i + 8])[::-1] for i in range(0, 32, 8)))


def _ipv6_interface(raw):
    if re.fullmatch('[0-9A-Fa-f]{32}', raw) is None:
        raise ValueError('invalid_ipv6_interface_address')
    return ipaddress.IPv6Address(int(raw, 16))


def native_dummy_mdns(hass):
    """Read Core's actual Zeroconf transport FDs and the kernel namespace."""
    from homeassistant.components.zeroconf import DATA_INSTANCE
    from homeassistant.components.zeroconf.models import HaAsyncZeroconf, HaZeroconf
    from zeroconf._engine import AsyncEngine

    interface = Path('/sys/class/net/r6dummy')
    if (not interface.exists() or (interface / 'device').exists()
            or not str(interface.resolve()).startswith('/sys/devices/virtual/net/')):
        raise ValueError('dummy_interface_unverified')
    index, link = (int((interface / key).read_text()) for key in ('ifindex', 'iflink'))
    if index <= 1 or index != link:
        raise ValueError('dummy_peer_or_index_unverified')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        address = socket.inet_ntoa(fcntl.ioctl(probe.fileno(), 0x8915,
                                               struct.pack('256s', b'r6dummy'))[20:24])
    if address != '192.0.2.1':
        raise ValueError('dummy_address_unverified')
    ipv6 = [_ipv6_interface(fields[0]) for line in Path('/proc/net/if_inet6').read_text().splitlines()
            if len(fields := line.split()) == 6 and fields[5] == 'r6dummy']
    if len(ipv6) != 1 or not ipv6[0].is_link_local:
        raise ValueError('dummy_ipv6_unverified')
    ipv4_routes = [line.split() for line in Path('/proc/net/route').read_text().splitlines()[1:]]
    if any(len(row) < 4 or row[0] not in ('lo', 'r6dummy') or row[1] == '00000000'
           for row in ipv4_routes):
        raise ValueError('dummy_ipv4_route_unverified')
    ipv6_routes = [line.split() for line in Path('/proc/net/ipv6_route').read_text().splitlines()]
    for row in ipv6_routes:
        if len(row) != 10 or row[-1] not in ('lo', 'r6dummy'):
            raise ValueError('dummy_ipv6_route_unverified')
        if int(row[0], 16) == 0 and int(row[1], 16) == 0:
            if row[-1] != 'lo' or not int(row[8], 16) & 0x200:
                raise ValueError('dummy_ipv6_default_route_unverified')
    zc = hass.data.get(DATA_INSTANCE)
    if (type(zc) is not HaAsyncZeroconf or type(zc.zeroconf) is not HaZeroconf
            or zc.zeroconf.done is not False or type(zc.zeroconf.engine) is not AsyncEngine):
        raise ValueError('dummy_zeroconf_unverified')
    engine = zc.zeroconf.engine
    if engine.zc is not zc.zeroconf or engine.loop is not hass.loop or len(engine.readers) != 3:
        raise ValueError('dummy_zeroconf_transport_unverified')
    inodes = []
    for reader in engine.readers:
        native = reader.transport.get_extra_info('socket')
        if native is None or type(native.fileno()) is not int or native.fileno() < 0:
            raise ValueError('dummy_zeroconf_fd_unverified')
        link_name = os.readlink('/proc/self/fd/' + str(native.fileno()))
        match = re.fullmatch(r'socket:\[(\d+)\]', link_name)
        if match is None:
            raise ValueError('dummy_zeroconf_inode_unverified')
        inodes.append(match.group(1))
    if len(set(inodes)) != 3:
        raise ValueError('dummy_zeroconf_duplicate_socket')
    seen = []
    for table in ('tcp', 'tcp6', 'udp', 'udp6'):
        for line in (Path('/proc/net') / table).read_text().splitlines()[1:]:
            fields = line.split()
            address_hex, port = fields[1].split(':')
            remote_hex, remote_port = fields[2].split(':')
            local = (ipaddress.IPv4Address(struct.pack('<I', int(address_hex, 16))) if table in ('tcp', 'udp')
                     else _ipv6_proc(address_hex))
            if local.is_loopback:
                continue
            if (table not in ('udp', 'udp6') or int(port, 16) != 5353 or int(remote_hex, 16) != 0
                    or int(remote_port, 16) != 0 or fields[3] != '07' or fields[9] not in inodes):
                raise ValueError('dummy_foreign_socket_unverified')
            if not (str(local) == '192.0.2.1' or local == ipaddress.IPv6Address('::') or local == ipv6[0]):
                raise ValueError('dummy_mdns_address_unverified')
            seen.append((table, fields[9], 'ipv4' if local.version == 4 else
                         'ipv6-wildcard' if local.is_unspecified else 'ipv6-linklocal', 5353, 0, '07'))
    if (len(seen) != 3 or {row[1] for row in seen} != set(inodes)
            or sorted(row[2] for row in seen) != ['ipv4', 'ipv6-linklocal', 'ipv6-wildcard']):
        raise ValueError('dummy_mdns_socket_membership_unverified')
    return {'kind': 'native-zeroconf-dummy-v1', 'ifindex': index, 'iflink': link,
            'virtual_without_device': True, 'ipv4': address, 'no_external_routes': True,
            'reader_inodes': sorted(inodes), 'sockets': sorted([list(row) for row in seen])}


def network_quiet(network):
    """Accept original loopback scope or exactly the independently bound dummy."""
    if (type(network) is not dict or network.get('unreadable') is not False
            or any(type(network.get(key)) is not int or network[key] < 0 for key in
                   ('unix_sockets', 'loopback_connections', 'nonloopback_sockets'))
            or network.get('foreign_processes') != [] or network.get('unix_sockets') != 0
            or network.get('loopback_connections') != 0):
        return False
    if network.get('interfaces_up') == ['lo']:
        return network.get('nonloopback_sockets') == 0 and 'dummy_mdns' not in network
    evidence = network.get('dummy_mdns')
    if (network.get('interfaces_up') != ['lo', 'r6dummy'] or type(evidence) is not dict
            or set(evidence) != {'kind', 'ifindex', 'iflink', 'virtual_without_device', 'ipv4',
                                 'no_external_routes', 'reader_inodes', 'sockets'}
            or evidence['kind'] != 'native-zeroconf-dummy-v1'
            or type(evidence['ifindex']) is not int or evidence['ifindex'] <= 1
            or type(evidence['iflink']) is not int or evidence['iflink'] != evidence['ifindex']
            or evidence['virtual_without_device'] is not True or evidence['no_external_routes'] is not True
            or evidence['ipv4'] != '192.0.2.1' or type(evidence['reader_inodes']) is not list
            or type(evidence['sockets']) is not list or type(network.get('nonloopback_sockets')) is not int
            or network['nonloopback_sockets'] != 3 or len(evidence['reader_inodes']) != 3
            or len(evidence['sockets']) != 3):
        return False
    readers, sockets = evidence['reader_inodes'], evidence['sockets']
    return (all(type(value) is str and re.fullmatch(r'\d+', value) for value in readers)
            and len(set(readers)) == 3
            and all(type(row) is list and len(row) == 6 and row[0] in ('udp', 'udp6')
                    and type(row[1]) is str and row[1] in readers
                    and row[2] in ('ipv4', 'ipv6-linklocal', 'ipv6-wildcard')
                    and type(row[3]) is int and row[3] == 5353
                    and type(row[4]) is int and row[4] == 0 and row[5] == '07' for row in sockets)
            and {row[1] for row in sockets} == set(readers)
            and sorted(row[2] for row in sockets) == ['ipv4', 'ipv6-linklocal', 'ipv6-wildcard']
            and sum(row[0] == 'udp' and row[2] == 'ipv4' for row in sockets) == 1
            and sum(row[0] == 'udp6' and row[2].startswith('ipv6-') for row in sockets) == 2)
