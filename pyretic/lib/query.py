################################################################################
# The Pyretic Project                                                          #
# frenetic-lang.org/pyretic                                                    #
# author: Joshua Reich (jreich@cs.princeton.edu)                               #
################################################################################
# Licensed to the Pyretic Project by one or more contributors. See the         #
# NOTICES file distributed with this work for additional information           #
# regarding copyright and ownership. The Pyretic Project licenses this         #
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

from pyretic.core.language import identity, match, union, DerivedPolicy, DynamicFilter, FwdBucket
import time
from threading import Thread

class LimitFilter(DynamicFilter):
    """A DynamicFilter that matches the first limit packets in a specified grouping.

    :param limit: the number of packets to be matched in each grouping.
    :type limit: int
    :param group_by: the fields by which to group packets.
    :type group_by: list string
    """
    def __init__(self,limit=None,group_by=[]):
        self.limit = limit
        self.group_by = group_by
        self.seen = {}
        self.done = []
        super(LimitFilter,self).__init__(identity)

    def get_pred_from_pkt(self, pkt):
        if self.group_by:    # MATCH ON PROVIDED GROUP_BY
            pred = match([(field,pkt[field]) for field in self.group_by])
        else:              # OTHERWISE, MATCH ON ALL AVAILABLE GROUP_BY
            pred = match([(field,pkt[field]) 
                              for field in pkt.available_group_by()])

    def update_policy(self,pkt):
        pred = self.get_pred_from_pkt(pkt)
        # INCREMENT THE NUMBER OF TIMES MATCHING PKT SEEN
        try:
            self.seen[pred] += 1
        except KeyError:
            self.seen[pred] = 1

        if self.seen[pred] == self.limit:
            val = {h : pkt[h] for h in self.group_by}
            self.done.append(match(val))
            self.policy = ~union(self.done)

    def __repr__(self):
        return "LimitFilter\n%s" % repr(self.policy)


class packets(DerivedPolicy):
    """A FwdBucket preceeded by a LimitFilter.

    :param limit: the number of packets to be matched in each grouping.
    :type limit: int
    :param group_by: the fields by which to group packets.
    :type group_by: list string
    """
    def __init__(self,limit=None,group_by=[]):
        self.fb = FwdBucket()
        self.register_callback = self.fb.register_callback
        if limit is None:
            super(packets,self).__init__(self.fb)
        else:
            self.limit_filter = LimitFilter(limit,group_by)
            self.fb.register_callback(self.limit_filter.update_policy)
            super(packets,self).__init__(self.limit_filter >> self.fb)
        
    def __repr__(self):
        return "packets\n%s" % repr(self.policy)

class counts(DynamicPolicy):
    """A CountBucket that returns distinct counts per grouping, defined by a set
    of header fields.

    :param interval: time period between successive pulls of switch statistics
    :type interval: some float
    :param group_by: list of grouping fields
    :type group_by: string list
    """
    def __init__(self, interval=None, group_by=[]):
        self.set_up_policy(group_by)
        self.set_up_stats()
        self.set_up_polling(interval)

    def set_up_policy(self, group_by):
        """Setup policy structure and basic callbacks."""
        self.bucket_policies = []
        self.groupby_filter = LimitFilter(1,group_by)
        self.fb = FwdBucket() # fb sees first packet of each new grouping
        self.fb.register_callback(self.groupby_filter.update_policy)
        self.fb.register_callback(self.init_countbucket)
        super(counts,self).__init__(self.groupby_filter >> self.fb)

    def set_up_stats(self):
        """Setup for pulling stats and related book-keeping."""
        self.callbacks = []
        self.bucket_dict = {}
        self.queried_preds = set([])
        self.reported_counts = {}
        from multiprocessing import Lock
        self.queried_preds = set([])
        self.queried_preds_lock = Lock()

    def set_up_polling(self,interval):
        """Setup polling of stats from switches."""
        if interval:
            print "do stuff"

    def init_countbucket(self, pkt):
        """When a packet from a previously unseen grouping arrives, set up new
        count buckets for the same.
        """
        pred = self.groupby_filter.get_pred_from_pkt(pkt)
        cb = CountBucket()
        cb.register_callback(self.collect_pred(pred))
        self.bucket_policies.append(pred >> cb)
        self.bucket_dict[pred] = cb
        self.policy = ((self.groupby_filter >> self.fb) +
                       union(self.bucket_policies))

    def collect_pred(self, pred):
        """Return a callback function specific to each grouping predicate."""
        def collect(pkt_byte_counts):
            with self.queried_preds_lock:
                if pred in self.queried_preds:
                    self.returned_counts[pred] = pkt_byte_counts
                    self.queried_preds.remove(pred)
            # Check if all queried buckets have returned
            if not self.queried_preds:
                for f in self.callbacks:
                    f(self.returned_counts)
        return collect

    def register_callback(self, fn):
        self.callbacks.append(fn)

    def pull_stats(self):
        """Pulls statistics from the switches corresponding to all groupings."""
        with self.queried_preds_lock:
            self.queried_preds = set(copy.deepcopy(self.bucket_dict.keys()))
            self.reported_counts = {}
        for pred in self.queried_preds:
            self.bucket_dict[pred].pull_stats()

    def __repr__(self):
        return "counts\n%s" % repr(self.policy)


class AggregateFwdBucket(FwdBucket):
    """An abstract FwdBucket which calls back all registered routines every interval
    seconds (can take positive fractional values) with an aggregate value/dict.
    If group_by is empty, registered routines are called back with a single aggregate
    value.  Otherwise, group_by defines the set of headers used to group counts which
    are then returned as a dictionary."""
    ### init : int -> List String
    def __init__(self, interval, group_by=[]):
        FwdBucket.__init__(self)
        self.interval = interval
        self.group_by = group_by
        if group_by:
            self.aggregate = {}
        else:
            self.aggregate = 0
        
        def report_count(callbacks,aggregate,interval):
            while(True):
                for callback in callbacks:
                    callback(aggregate)
                time.sleep(interval)

        self.query_thread = Thread(target=report_count,args=(self.callbacks,self.aggregate,self.interval))
        self.query_thread.daemon = True
        self.query_thread.start()

    def aggregator(self,aggregate,pkt):
        raise NotImplementedError

    ### update : Packet -> unit
    def update_aggregate(self,pkt):
        if self.group_by:
            from pyretic.core.language import match
            groups = set(self.group_by) & set(pkt.available_fields())
            pred = match([(field,pkt[field]) for field in groups])
            try:
                self.aggregate[pred] = self.aggregator(self.aggregate[pred],pkt)
            except KeyError:
                self.aggregate[pred] = self.aggregator(0,pkt)
        else:
            self.aggregate = self.aggregator(self.aggregate,pkt)

    def eval(self, pkt):
        self.update_aggregate(pkt)
        return set()


class count_packets(AggregateFwdBucket):
    """AggregateFwdBucket that calls back with aggregate count of packets."""
    def aggregator(self,aggregate,pkt):
        return aggregate + 1


class count_bytes(AggregateFwdBucket):
    """AggregateFwdBucket that calls back with aggregate bytesize of packets."""
    def aggregator(self,aggregate,pkt):
        return aggregate + pkt['header_len'] + pkt['payload_len']
