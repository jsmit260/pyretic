################################################################################
# The Frenetic Project                                                         #
# frenetic@frenetic-lang.org                                                   #
################################################################################
# Licensed to the Frenetic Project by one or more contributors. See the        #
# NOTICES file distributed with this work for additional information           #
# regarding copyright and ownership. The Frenetic Project licenses this        #
# file to you under the following license.                                     #
#                                                                              #
# Redistribution and use in source and binary forms, with or without           #
# modification, are permitted provided the following conditions are met:       #
# - Redistributions of source code must retain the above copyright             #
#   notice, this list of conditions and the following disclaimer.              #
# - Redistributions in binary form must reproduce the above copyright          #
#   notice, this list of conditions and the following disclaimer in            #
#   the documentation or other materials provided with the distribution.       #
# - The names of the copyright holds and contributors may not be used to       #
#   endorse or promote products derived from this work without specific        #
#   prior written permission.                                                  #
#                                                                              #
# Unless required by applicable law or agreed to in writing, software          #
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT    #
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the     #
# LICENSE file distributed with this work for specific language governing      #
# permissions and limitations under the License.                               #
################################################################################

# This module is designed for import *.
import functools
import itertools
import socket
import struct
from abc import ABCMeta, abstractmethod, abstractproperty
from collections import Counter
from numbers import Integral

from bitarray import bitarray

from frenetic import util, generators as gs
from frenetic.network import *
from frenetic.generators import Event
from frenetic.util import frozendict, Data, merge_dicts

################################################################################
# Matching and wildcards
################################################################################

class Matchable(object):
    """Assumption: the binary operators are passed in the same class as the invoking object."""
    __metaclass__ = ABCMeta

    @classmethod
    @abstractmethod
    def top(cls):
        """Return the matchable greater than all other matchables of the same class. """

    @abstractmethod
    def __and__(self, other):
        """Return the intersection of two matchables of the same class.
        Return value is None if there is no intersection."""

    @abstractmethod
    def __le__(self, other):
        """Return true if `other' matches every object `self' does."""

    @abstractmethod
    def match(self, other):
        """Return true if we match `other'.""" 

# XXX some of these should be requirements on matchable.
class MatchableMixin(object):
    """Helper"""
    def disjoint_with(self, other):
        """Return true if there is no object both matchables match."""
        return self & other is None
    
    def overlaps_with(self, other):
        """Return true if there is an object both matchables match."""
        return not self.overlaps_with(other)
        
    def __eq__(self, other):
        return self <= other and other <= self

    def __ne__(self, other):
        """Implemented in terms of __eq__"""
        return not self == other


class Approx(object):
    """Interface for things which can be approximated."""
    __metaclass__ = ABCMeta

    @abstractmethod
    def overapprox(self, overapproxer):
        """Docs here."""

    @abstractmethod
    def underapprox(self, underapproxer):
        """Docs here."""

@util.cached
def Wildcard(width_):
    @functools.total_ordering
    class Wildcard_(MatchableMixin, Data("prefix mask")):
        """Full wildcards."""

        width = width_

        @classmethod
        def is_wildstr(cls, value):
            return isinstance(value, basestring) and len(value) == cls.width and set(value) <= set("?10")

        def __new__(cls, prefix, mask=None):
            """Create a wildcard. Prefix is a binary string.
            Mask can either be an integer (how many bits to mask) or a binary string."""

            if cls.is_wildstr(prefix):
                bprefix = bitarray(prefix.replace("?", "0"))
                bmask = bitarray(prefix.replace("1", "0").replace("?", "1"))
                prefix = bprefix
                mask = bmask
            elif isinstance(prefix, Wildcard_):
                (prefix, mask) = prefix.prefix, prefix.mask
            else:
                if isinstance(prefix, FixedWidth):
                    prefix = prefix.to_bits()
                if isinstance(mask, FixedWidth):
                    mask = mask.to_bits()
                elif mask is None:
                    mask = bitarray([False] * len(prefix))
                
                assert len(prefix) == cls.width == len(mask), "mask and prefix must be same length"
                
            return super(Wildcard_, cls).__new__(cls, prefix, mask)

        def __hash__(self):
            return hash((self.prefix.tobytes(), self.mask.tobytes()))

        def __repr__(self):
            l = []
            for pb, mb in zip(self.prefix, self.mask):
                if mb:
                    l.append("?")
                else:
                    l.append(str(int(pb)))
            return "".join(l)
        
        @classmethod
        def top(cls):
            prefix = bitarray(cls.width)
            prefix.setall(False)
            mask = bitarray(cls.width)
            mask.setall(False)
            return cls(prefix, mask)

        def match(self, other):
            return other.to_bits() | self.mask == self._normalize()

        def __and__(self, other):
            if self.overlaps_with(other):
                return self.__class__(self._normalize() & other._normalize(),
                                      self.mask & other.mask)

        def overlaps_with(self, other):
            c_mask = self.mask | other.mask
            return self.prefix | c_mask == other.prefix | c_mask

        def __le__(self, other):
            return (self.mask & other.mask == other.mask) and \
                (self.prefix | self.mask == other.prefix | self.mask)

        def _normalize(self):
            """Return a bitarray, masked."""
            return self.prefix | self.mask

    Matchable.register(Wildcard_)
    Wildcard_.__name__ += repr(width_)
    
    return Wildcard_

@util.cached
def MatchExact(match_cls):
    class MatchExact_(Wildcard(match_cls.width)):
        def __new__(cls, *v):
            try:
                # XXX ugh.
                w = super(MatchExact_, cls).__new__(cls, *v)
                assert w is not None
                return w
            except:
                bits = match_cls(*v).to_bits()
                return super(MatchExact_, cls).__new__(cls, bits) 

    MatchExact_.__name__ += match_cls.__name__
    return MatchExact_

class IPWildcard(Wildcard(32)):
    def __new__(cls, ipexpr, mask=None):
        if isinstance(ipexpr, basestring):
            parts = ipexpr.split("/")

            if len(parts) == 2:
                ipexpr = parts[0]
                try:
                    mask = int(parts[1], 10)
                except ValueError:
                    mask = parts[1]
            elif len(parts) != 1:
                raise ValueError

            if mask is None:
                prefix = bitarray()
                mask = bitarray(32)
                (a, b, c, d) = ipexpr.split(".")
                mask.setall(False)
                if a == "*":
                    mask[0:8] = True
                    prefix.extend("00000000")
                else:
                    prefix.frombytes(struct.pack("!B", int(a)))
                if b == "*":
                    mask[8:16] = True
                    prefix.extend("00000000")
                else:
                    prefix.frombytes(struct.pack("!B", int(b)))
                if c == "*":
                    mask[16:24] = True
                    prefix.extend("00000000")
                else:
                    prefix.frombytes(struct.pack("!B", int(c)))
                if d == "*":
                    mask[24:32] = True
                    prefix.extend("00000000")
                else:
                    prefix.frombytes(struct.pack("!B", int(d)))
            elif isinstance(mask, Integral):
                prefix = IP(ipexpr)
                bmask = bitarray(32)
                bmask.setall(True)
                bmask[0:mask] = False
                mask = bmask
            elif isinstance(mask, basestring):
                prefix = IP(ipexpr)
                mask = IP(mask).to_bits()
                mask.invert()
        else:
            prefix = ipexpr
                
        return super(IPWildcard, cls).__new__(cls, prefix, mask)
            
       

################################################################################
# Predicates
################################################################################



class Predicate(object):
    """Top-level abstract class for predicates."""
   
    def __and__(self, other):
        if isinstance(other, Policy):
            return PolRestrict(self, other)
        else:
            return PredIntersection(self, other)
    def __or__(self, other):
        return PredUnion(self, other)
    def __sub__(self, other):
        return PredDifference(self, other)
    def __invert__(self):
        return PredNegation(self)
    def __eq__(self, other):
        raise NotImplementedError
    def __ne__(self, other):
        raise NotImplementedError
    def eval(self, packet):
        env = frozendict({"_." + k : v for k, v in packet.header.iteritems()})
        return self._eval(packet, env)

class PredAll(Predicate):
    """The always-true predicate."""
    def __repr__(self):
        return "all_packets"
    def _eval(self, packet, env):
        return True
      
class PredNone(Predicate):
    """The always-false predicate."""
    def __repr__(self):
        return "no_packets"
    def _eval(self, packet, env):
        return False
    
class PredMatch(Predicate, Data("varname pattern")):
    """A basic predicate matching against a single field"""
    def __repr__(self):
        return "%s == %s" % (self.varname, self.pattern)
    def _eval(self, packet, env):
        if self.pattern is None:
            return self.varname not in env
        else:
            if self.varname in env:
                return self.pattern.match(env[self.varname])
            else:
                return False
                
class PredUnion(Predicate, Data("left right")):
    """A predicate representing the union of two predicates."""
    def __repr__(self):
        return "PredUnion:\n%s" % util.repr_plus([self.left, self.right])
    def _eval(self, packet, env):
        return self.left._eval(packet, env) or self.right._eval(packet, env)
        
class PredIntersection(Predicate, Data("left right")):
    """A predicate representing the intersection of two predicates."""
    def __repr__(self):
        return "PredIntersection:\n%s" % util.repr_plus([self.left, self.right])
    def _eval(self, packet, env):
        return self.left._eval(packet, env) and self.right._eval(packet, env)

class PredDifference(Predicate, Data("left right")):
    """A predicate representing the difference of two predicates."""
    def __repr__(self):
        return "PredDifference:\n%s" % util.repr_plus([self.left, self.right])
    def _eval(self, packet, env):
        return self.left._eval(packet, env) and not self.right._eval(packet, env)

class PredNegation(Predicate, Data("pred")):
    """A predicate representing the difference of two predicates."""
    def __repr__(self):
        return "PredNegation:\n%s" % util.repr_plus(self)
    def _eval(self, packet, env):
        return not self.pred._eval(packet, env)

################################################################################
# Actions (these are internal data structures)
################################################################################

class Action(Counter):
    def enumerate_eval(self, packet):
        packets = []
        for moddict in self.elements():
            p = packet.update_header_fields(**moddict)
            packets.append((moddict, p))
        return packets

    def eval(self, packet):
        return [p for moddict, p in self.enumerate_eval(packet)]
        
                
################################################################################
# Policies
################################################################################

class Policy(object):
    """Top-level abstract description of a static network program."""
    def __or__(self, other):
        return PolUnion(self, other)
    def __and__(self, other):
        assert isinstance(other, Predicate)
        return PolRestrict(other, self)
    def __sub__(self, pred):
        return PolRemove(self, pred)
    def __rshift__(self, pol):
        return PolComposition(self, pol)
    def __eq__(self, other):
        raise NotImplementedError
    def __ne__(self, other):
        raise NotImplementedError
    def eval(self, packet):
        act = Action()
        env = frozendict({"_." + k : v for k, v in packet.header.iteritems()})
        self._eval(packet, env, act)
        return act
    def packets_to_send(self, packet):
        return self.eval(packet).eval(packet)
    
class PolDrop(Policy):
    """Policy that drops everything."""
    def __repr__(self):
        return "drop"
    def _eval(self, packet, env, act):
        pass

class PolPassthrough(Policy):
    def __repr__(self):
        return "passthrough"
    def _eval(self, packet, env, act):
        act[frozendict()] += 1
        
class PolModify(Policy, Data("field value")):
    """Policy that drops everything."""
    def __repr__(self):
        return "modify %s <- %s" % self
    def _eval(self, packet, env, act):
        act[frozendict({self.field: self.value})] += 1
    
class PolRestrict(Policy, Data("predicate policy")):
    """Policy for mapping a single predicate to a list of actions."""
    def __repr__(self):
        return "PolRestrict:\n" + util.repr_plus([self.predicate, self.policy])
    def _eval(self, packet, env, act):
        if self.predicate._eval(packet, env):
            self.policy._eval(packet, env, act)

class PolLet(Policy, Data("varname policy body")):
    def __repr__(self):
        return "PolLet %s:\n%s" % (self.varname, util.repr_plus([self.policy, self.body]))
    def _eval(self, packet, env, act):
        act_ = Action()
        self.policy._eval(packet, env, act_)
        for n_packet in act_.eval(packet):
            n_env = env.update({self.varname + "." + k: v
                                for k, v in n_packet.header.iteritems()})
            self.body._eval(n_packet, n_env, act)
        
class PolComposition(Policy, Data("left right")):
    def __repr__(self):
        return "PolComposition:\n%s" % util.repr_plus([self.left, self.right])
    def _eval(self, packet, env, act):
        act_ = Action()
        self.left._eval(packet, env, act_)
        for moddict, n_packet in act_.enumerate_eval(packet):
            n_act = Action()
            n_env = env.update({"_." + k : v for k, v in n_packet.header.iteritems()})
            self.right._eval(n_packet, n_env, n_act)
            for moddict_, count in n_act.iteritems():
                n_moddict = moddict_.update(moddict)
                act[n_moddict] += count
            
class PolUnion(Policy, Data("left right")):
    def __repr__(self):
        return "PolUnion:\n%s" % util.repr_plus([self.left, self.right])
                                             
    def _eval(self, packet, env, act):
        self.left._eval(packet, env, act)
        self.right._eval(packet, env, act)
        
class PolRemove(Policy, Data("policy predicate")):
    def __repr__(self):
        return "PolRemove:\n%s" % util.repr_plus([self.left, self.right])
    def _eval(self, packet, env, act):
        if not self.predicate._eval(packet, env):
            self.policy._eval(packet, env, act)

################################################################################
# Lifts
################################################################################

header_to_matchable_lift = dict(
    switch=MatchExact(Switch),
    vswitch=MatchExact(Switch),
    inport=MatchExact(Port),
    outport=MatchExact(Port),
    vinport=MatchExact(Port),
    voutport=MatchExact(Port),
    srcmac=MatchExact(MAC),
    dstmac=MatchExact(MAC),
    vlan=MatchExact(FixedInt(12)),
    vlan_pcp=MatchExact(FixedInt(3)),
    srcip=IPWildcard,
    dstip=IPWildcard,
    srcport=MatchExact(FixedInt(16)),
    dstport=MatchExact(FixedInt(16)),
    protocol=MatchExact(FixedInt(8)),
    tos=MatchExact(FixedInt(6)),
    type=MatchExact(FixedInt(16)),)

def lift_matchable_kv(k, v):
    cls = header_to_matchable_lift.get(k)

    if cls is None:
        assert isinstance(v, Matchable)
        return v
    else:
        if not isinstance(v, tuple):
            v = (v,)
        return cls(*v)
    
################################################################################
# Predicates and policies
################################################################################

class FieldMatch(Data("prefix name")):
    def qual_name(self):
        return "%s.%s" % self
    def __eq__(self, other):
        other = lift_matchable_kv(self.name, other)
        return PredMatch(self.qual_name(), other)
    def __ne__(self, other):
        return ~(self == other)
    def is_missing(self):
        return PredMatch(self.qual_name(), None)
        
class PacketMatch(Data("prefix")):
    def __getattr__(self, attr):
        return FieldMatch(self.prefix, attr)

_ = PacketMatch("_")

#

all_packets = PredAll()
no_packets = PredNone()

def let(policy, body):
    assert hasattr(body, "func_code"), "must be a function (literally)"
    name = body.func_code.co_varnames[0]
    return PolLet(name,
                  policy,
                  body(PacketMatch(name)))
        
def if_(pred, t_branch, f_branch):
    return pred & t_branch | ~pred & f_branch

drop = PolDrop()
passthrough = PolPassthrough()

def modify(**kwargs):
    policy = passthrough
    for k, v in kwargs.iteritems():
        if v is not None:
            v = lift_fixedwidth_kv(k, v)
        policy = policy >> PolModify(k, v)
    return policy

def fwd(port):
    return modify(outport=port) 

flood = fwd(Port.flood_port)

def enum(*args):
    fnargs = args[:-1]
    fn = args[-1]

    fields = [ field for field, values in fnargs ]
    value_row = itertools.product(*[values for field, values in fnargs])

    policy = drop
    
    for vr in value_row:
        pred = all_packets
        for i, v in enumerate(vr):
            pred &= fields[i] == v
        policy |= pred & fn(*vr)
        
    return policy
        
################################################################################
# Network
################################################################################

class Network(object):
    def __init__(self):
        self.switch_joins = gs.Event()
        self.switch_parts = gs.Event()
        
        self.policy_b = gs.Behavior(drop)
        self.policy_changes = self.policy_b # used only for iter method
        self.sub_policies = {}
        
        super(Network, self).__init__()

    def query(self, pred, fields=(), time=None):
        b = Bucket(fields, time)
        b.sub_network = fork_sub_network(self)
        b.sub_network.install_policy(pred & fwd(b))
        return b
        
    def install_policy(self, policy):
        self.sub_policies[self] = policy
        self.policy_b.set(self._aggregate_policy())
        
    def get_policy(self):
        return self.policy_b.get()

    def install_sub_policies(self, sub_gen):
        it = iter(sub_gen) # Don't leave creating the iterator up to timing.
        def adder():
            for policy in it:
                self.sub_policies[sub_gen] = policy
                self.policy_b.set(self._aggregate_policy())
        gs.run(adder)
    
    def _aggregate_policy(self):
        pol = drop
        for policy in self.sub_policies.itervalues():
            pol |= policy
        return pol
        
def add_sub_network(super_network, sub_network):
    super_network.install_sub_policies(sub_network.policy_changes)
        
def fork_sub_network(network):
    sub_net = Network()
    sub_net.switch_joins = network.switch_joins
    sub_net.switch_parts = network.switch_parts

    add_sub_network(network, sub_net)
    
    return sub_net


################################################################################
# Virtualization
################################################################################

def fork_virtual_network(network, vinfo, ingress_policy, physical_policy):
    sub_net = Network()
    vlan_db = generate_vlan_db(0, vinfo)

    network.install_sub_policies(
        virtualize_policy(vlan_db, ingress_policy, physical_policy, policy)
        for policy in sub_net.policy_changes)
    
    return sub_net

def generate_vlan_db(start_vlan, vinfo):
    vlan_to_vheaders = {}
    vheaders_to_vlan = {}
    
    for si, vswitch in enumerate(vinfo):
        for ipi, vinport in enumerate(vinfo[vswitch]):
            for opi, voutport in enumerate(vinfo[vswitch]):
                vlan_to_vheaders[start_vlan + si + ipi + opi] = (vswitch, vinport, voutport)
                vheaders_to_vlan[(vswitch, vinport, voutport)] = start_vlan + si + ipi + opi

    return (vinfo, start_vlan, vlan_to_vheaders, vheaders_to_vlan)

def vheaders_to_vlan_policy(vlan_db):
    (vinfo, start_vlan, vlan_to_vheaders, vheaders_to_vlan) = vlan_db
    
    return (_.vswitch.is_missing() & modify(vlan=None) # if we are no longer virtualized, remove vlan.
            | enum((_.vswitch, vinfo.iterkeys()), # otherwise, encode.
                   lambda s:
                   enum((_.vinport, vinfo[s]),
                        (_.voutport, vinfo[s]),
                        lambda ip, op:
                        modify(vswitch=None,
                               vinport=None,
                               voutport=None,
                               vlan=vheaders_to_vlan[(s, ip, op)]))))
    
def vlan_to_vheaders_policy(vlan_db):
    (vinfo, start_vlan, vlan_to_vheaders, vheaders_to_vlan) = vlan_db

    def vlan_dict_helper(vlan):
        (vswitch, vinport, voutport) = vlan_to_vheaders[vlan]
        return modify(vlan=None, vswitch=vswitch, vinport=vinport, voutport=voutport)
    
    return enum((_.vlan, vlan_to_vheaders), vlan_dict_helper)
    
def pre_vheaders_to_headers_policy(vlan_db):
    (vinfo, start_vlan, vlan_to_vheaders, vheaders_to_vlan) = vlan_db
    
    return enum((_.vswitch, vinfo.iterkeys()), 
                lambda s:
                enum((_.vinport, vinfo[s]),
                     lambda ip: modify(switch=s,
                                       inport=ip,
                                       vswitch=None,
                                       vinport=None)))

def headers_to_post_vheaders(vlan_db, x):
    (vinfo, start_vlan, vlan_to_vheaders, vheaders_to_vlan) = vlan_db
    
    return enum((x.vswitch, vinfo.iterkeys()),
                lambda s:
                enum((x.outport, vinfo[s]),
                     lambda op: modify(voutport=op)))
        
def virtualize_policy(vlan_db, ingress_policy, physical_policy, policy):
    """
    - `vinfo' is a mapping from switch to set of ports.
    - `start_vlan, ...` - see generate_vlan_db
    - `ingress_policy' is written in terms of the physical network, and tries to
       detect whether a packet is at the ingress of a virtual switch. If the packet
       is at the ingress of a virtual switch, then modify the vswitch and vinport of the
       packet to be the current virtual switch and the inport we are at, respectively .
    - `policy' is written in terms of the virtual network, and modifies the outport field.
       We will modify the voutport of the packet to be the outport returned.
    - `physical_policy' is written in terms of the physical network, and tries to
       forward packets along the fabric of the virtual switch until voutport is reached.
       When the packet leaves vswitch, the v* headers must be removed.

       (Implementation detail: removing vswitch is sufficient).

    Returns the virtualization of `policy' with respect to the other parameters.
    """

    return (if_(_.vlan.is_missing(), # if the vlan isnt set, then we need to find out the v* headers.
                    (ingress_policy >> # set vswitch and vinport
                    # now make the virtualization transparent to the tenant's policy to get the outport
                     let(pre_vheaders_to_headers_policy(vlan_db) >> policy,
                         lambda x: headers_to_post_vheaders(vlan_db, x))),
                  # However, if vlan IS set, re-set the v* headers.
                  vlan_to_vheaders_policy(vlan_db))
            # Pipe the packet with appropriate v* headers to the physical policy for processing
            >> physical_policy
            # and translate the v* headers to a vlan value, since the real network
            # doesn't understand our custom headers.
            >> vheaders_to_vlan_policy(vlan_db))
          