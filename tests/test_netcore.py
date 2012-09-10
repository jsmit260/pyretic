
import pytest

from tests.common import *
from frenetic.netcore import *
from frenetic.netcore import _
from frenetic.network import *
from frenetic.generators import *


################################################################################
# Matchable junk
################################################################################

w = Wildcard(8)(bitarray("00000000"), bitarray("00000000"))
w2 = Wildcard(8)(bitarray("00000000"), bitarray("11111111"))

def test_Wildcard_match():
    assert w <= w
    assert w2 > w

def test_Wildcard_intersect():
    assert w & w2 == w2 & w

def test_MatchExact():
    return MatchExact(Switch)(10).match(Switch(10))

ip = IPWildcard("1.2.3.*")
ip2 = IPWildcard("1.2.3.4", "255.255.255.0")

def test_IPWildcard():    
    assert ip == ip2
    assert ip <= ip2
    assert ip2 <= ip
    assert not ip != ip2
    
    assert ip2 == IPWildcard("1.2.3.8", "255.255.255.0")
    assert not IPWildcard("255.255.255.255").match(IP("255.255.255.252"))


################################################################################
# Predicates
################################################################################


def test_Predicate_eval():
    p = packets[0]
    assert all_packets.eval(p)
    assert not no_packets.eval(p)

    assert (_.srcip == "1.2.3.4").eval(packets[1])
    assert (_.srcip == "1.2.3.*").eval(packets[1])
    assert (_.srcip == IP("1.2.3.4")).eval(packets[1])
    assert (_.srcip == IPWildcard("1.2.*.4")).eval(packets[1])
    assert not (_.dstport == 30).eval(packets[1])
    assert (_.dstport == Port(700)).eval(packets[1])


def test_Action():
    assert not drop.packets_to_send(packets[1])
    p = modify(srcport=100, dstport=100).packets_to_send(packets[1])[0]
    assert p.srcport == 100 and p.dstport == 100


let_pol1 = let(modify(dstport=1), lambda p: ((_.dstport == 700) & (p.dstport == 1)) & fwd(100))

def test_Let():
    assert let_pol1.packets_to_send(packets[1])[0].outport == Port(100)
    assert let_pol1.packets_to_send(packets[1])[0].dstport == 700

comp_pol1 = modify(dstport=1) >> ((_.dstport == 1) & (_.srcport == 30) & fwd(100))
    
def test_Composition():
    assert comp_pol1.packets_to_send(packets[1])[0].outport == Port(100)

def test_fwd():
    for packet in packets:
        assert fwd(1).packets_to_send(packet)[0].outport == Port(1)

def test_match_ips():
    assert (_.dstip == "127.0.0.1").eval(packets[1])
    assert (_.dstip == "127.*.*.*").eval(packets[1])
    assert (_.dstip == "*.*.*.1").eval(packets[1])
    assert (_.dstip == "127.0.0.1/32").eval(packets[1])
    assert (_.dstip == "127.0.0.255/24").eval(packets[1])
    assert not (_.dstip == "124.0.0.255/24").eval(packets[1])
    assert (_.dstip == "127.0.0.255/24").eval(packets[1])
    assert (_.dstip == ("127.0.0.255", 24)).eval(packets[1])
    assert (_.dstip == ("127.0.0.255", "255.255.255.0")).eval(packets[1])
    assert (_.dstip == "127.0.0.255/255.255.255.0").eval(packets[1])

    assert _.meow.is_missing().eval(packets[1])
    assert not _.srcip.is_missing().eval(packets[1])
    
def test_match_ints():    
    assert (_.srcport == 30).eval(packets[1])
    assert not (_.srcport == 31).eval(packets[1])
    assert not (_.srcport == "1000100010001000").eval(packets[1])
    assert not (_.srcport == "???????????????1").eval(packets[1])
    assert     (_.srcport == "????????????????").eval(packets[1])
    assert     (_.srcport == "???????????1111?").eval(packets[1])
    assert     (_.srcport == "0000000000011110").eval(packets[1])


# Test virtualization
#


def test_virtualization_works():
    vinfo = {Switch(1): [1, 2, 3, 4, 5]}
    vdb = generate_vlan_db(0, vinfo)
    virtualize_policy(vdb, drop, drop, fwd(3))


def test_virtualization():
    from examples.virtualized_monitor import v_signature, get_ingress_policy, get_physical_policy, setup_virtual_network

    vlan_db = generate_vlan_db(0, v_signature)
    user_policy = fwd(3)

    p = packets[2]
    p2 = p.update_header_fields(vswitch=1, vinport=1)

    assert get_ingress_policy().packets_to_send(p) == [p2]

    p3 = p.update_header_fields(switch=1, inport=1)
    assert pre_vheaders_to_headers_policy(vlan_db).packets_to_send(p2) == [p3]

    p4 = p3.update_header_fields(outport=3)
    assert user_policy.packets_to_send(p3) == [p4]

    p5 = p2.update_header_fields(voutport=3)
    # Can't do this, need a testing mechanism for it!
    # assert let(passthrough, lambda x: headers_to_post_vheaders(vlan_db, x)).packets_to_send(p2) 

    p6 = p5.update_header_fields(outport=3)
    assert get_physical_policy().packets_to_send(p5) == [p6]

    p7 = p6.update_header_fields(vswitch=None, vinport=None, voutport=None,
                                 vlan=vlan_db[3][(Switch(1), Port(1), Port(3))])
    
    vp = vheaders_to_vlan_policy(vlan_db)
    p7_ = vp.packets_to_send(p6)
    assert p7_ == [p7]
    

    n = Network()
    vn = setup_virtual_network(n)
    vn.install_policy(user_policy)

    import time
    time.sleep(0.0001)

    assert n.get_policy().packets_to_send(p) == [p7]

    
    
################################################################################
# Test networks
################################################################################

def test_in_place():
    pass

def test_Network():
    n = Network()
    assert not n.get_policy().packets_to_send(packets[0])

    n_fork = fork_sub_network(n)
    n_fork.install_policy(fwd(10))

    assert isinstance(n.get_policy(), Policy)

    import time
    time.sleep(0.01)

    assert n.get_policy().packets_to_send(packets[0])[0].outport == Port(10)

    