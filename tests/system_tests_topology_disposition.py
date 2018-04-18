#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

import unittest, os, json
from subprocess      import PIPE, STDOUT
from proton          import Message, PENDING, ACCEPTED, REJECTED, RELEASED, SSLDomain, SSLUnavailable, Timeout
from system_test     import TestCase, Qdrouterd, main_module, DIR, TIMEOUT, Process
from proton.handlers import MessagingHandler
from proton.reactor  import Container, AtMostOnce, AtLeastOnce, DynamicNodeProperties, LinkOption, ApplicationEvent, EventInjector
from proton.utils    import BlockingConnection
from qpid_dispatch.management.client import Node

import time
import datetime
import pdb
import inspect



# PROTON-828:
try:
    from proton import MODIFIED
except ImportError:
    from proton import PN_STATUS_MODIFIED as MODIFIED




#================================================
# Helper classes for all tests.
#================================================

class Stopwatch ( object ) :

    def __init__ ( self, name, timer, initial_time, repeat_time ) :
        self.name         = name
        self.timer        = timer
        self.initial_time = initial_time
        self.repeat_time  = repeat_time



class Timeout(object):
    """
    Named timeout object can handle multiple simultaneous
    timers, by telling the parent which one fired.
    """
    def __init__ ( self, parent, name ):
        self.parent = parent
        self.name   = name

    def on_timer_task ( self, event ):
        self.parent.timeout ( self.name )



class ManagementMessageHelper ( object ) :
    """
    Format management messages.
    """
    def __init__ ( self, reply_addr ) :
        self.reply_addr = reply_addr

    def make_connector_query ( self, connector_name ) :
        props = {'operation': 'READ', 'type': 'org.apache.qpid.dispatch.connector', 'name' : connector_name }
        msg = Message ( properties=props, reply_to=self.reply_addr )
        return msg

    def make_connector_delete_command ( self, connector_name ) :
        props = {'operation': 'DELETE', 'type': 'org.apache.qpid.dispatch.connector', 'name' : connector_name }
        msg = Message ( properties=props, reply_to=self.reply_addr )
        return msg

    def make_router_link_query ( self ) :
        props = { 'count':      '100',
                  'operation':  'QUERY',
                  'entityType': 'org.apache.qpid.dispatch.router.link',
                  'name':       'self',
                  'type':       'org.amqp.management'
                }
        attrs = []
        attrs.append ( unicode('linkType') )
        attrs.append ( unicode('linkDir') )
        attrs.append ( unicode('linkName') )
        attrs.append ( unicode('owningAddr') )
        attrs.append ( unicode('capacity') )
        attrs.append ( unicode('undeliveredCount') )
        attrs.append ( unicode('unsettledCount') )
        attrs.append ( unicode('acceptedCount') )
        attrs.append ( unicode('rejectedCount') )
        attrs.append ( unicode('releasedCount') )
        attrs.append ( unicode('modifiedCount') )

        msg_body = { }
        msg_body [ 'attributeNames' ] = attrs
        return Message ( body=msg_body, properties=props, reply_to=self.reply_addr )



#================================================
# END Helper classes for all tests.
#================================================





#================================================================
#     Setup
#================================================================


class TopologyDispositionTests ( TestCase ):
    """
    The disposition guarantee is that the sender should shortly know
    how its messages have been disposed: whether they have been
    accepted, released, or modified.
    These tests ensure that the disposition guarantee survives
    disruptions in router network topology.
    """

    @classmethod
    def setUpClass(cls):
        super(TopologyDispositionTests, cls).setUpClass()


        cls.routers = []


        def router(name, more_config):

            config = [ ('router',  {'mode': 'interior', 'id': name}),
                       ('address', {'prefix': 'closest',   'distribution': 'closest'}),
                       ('address', {'prefix': 'balanced',  'distribution': 'balanced'}),
                       ('address', {'prefix': 'multicast', 'distribution': 'multicast'})
                     ]    \
                     + more_config

            config = Qdrouterd.Config(config)

            cls.routers.append(cls.tester.qdrouterd(name, config, wait=True))


        client_ports = dict()
        client_ports [ 'A' ] = cls.tester.get_port()
        client_ports [ 'B' ] = cls.tester.get_port()
        client_ports [ 'C' ] = cls.tester.get_port()
        client_ports [ 'D' ] = cls.tester.get_port()

        inter_router_ports = dict()
        inter_router_ports [ 'A' ] = cls.tester.get_port()
        inter_router_ports [ 'B' ] = cls.tester.get_port()
        inter_router_ports [ 'C' ] = cls.tester.get_port()

        #
        #
        #  Topology of the 4-mesh, with costs of connections marked.
        #  Tail of arrow indicates initiator of connection.
        #  (The diagonal connections do not look very much like arrows, I fear...)
        #
        #                1
        #         D ----------> A
        #         | \         > ^
        #         | 20\   50/   |
        #         |     \ /     |
        #      1  |     / \     | 100
        #         |   /     \   |
        #         v /         > |
        #         C ----------> B
        #                1
        #

        cls.cost = dict()
        cls.cost [ 'AB' ] = 100
        cls.cost [ 'AC' ] =  50
        cls.cost [ 'AD' ] =   1
        cls.cost [ 'BC' ] =   1
        cls.cost [ 'BD' ] =  20
        cls.cost [ 'CD' ] =   1

        # Add an extra, high-cost connection between A and D.
        # This will be deleted in the first test.
        cls.cost [ 'AD2' ] =  11

        client_link_capacity       = 1000
        inter_router_link_capacity = 1000

        router ( 'A',
                 [
                    ( 'listener',
                      { 'port': client_ports['A'],
                        'role': 'normal',
                        'stripAnnotations': 'no',
                        'linkCapacity' : client_link_capacity
                      }
                    ),
                    ( 'listener',
                      { 'role': 'inter-router',
                        'port': inter_router_ports [ 'A' ],
                        'stripAnnotations': 'no',
                        'linkCapacity' : inter_router_link_capacity
                      }
                    )
                 ]
               )


        router ( 'B',
                 [
                    ( 'listener',
                      { 'port': client_ports['B'],
                        'role': 'normal',
                        'stripAnnotations': 'no',
                        'linkCapacity' : client_link_capacity
                      }
                    ),
                    ( 'listener',
                      { 'role': 'inter-router',
                        'port': inter_router_ports [ 'B' ],
                        'stripAnnotations': 'no',
                        'linkCapacity' : inter_router_link_capacity
                      }
                    ),
                    # The names on the connectors are what allows me to kill them later.
                    ( 'connector',
                      {  'name': 'AB_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'A' ],
                         'verifyHostname': 'no',
                         'cost':  cls.cost['AB'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    )
                 ]
               )


        router ( 'C',
                 [
                    ( 'listener',
                      { 'port': client_ports['C'],
                        'role': 'normal',
                        'stripAnnotations': 'no',
                        'linkCapacity' : client_link_capacity
                      }
                    ),
                    ( 'listener',
                      { 'role': 'inter-router',
                        'port': inter_router_ports [ 'C' ],
                        'stripAnnotations': 'no',
                        'linkCapacity' : inter_router_link_capacity
                      }
                    ),
                    ( 'connector',
                      {  'name': 'AC_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'A' ],
                         'verifyHostname': 'no',
                         'cost' : cls.cost['AC'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    ),
                    ( 'connector',
                      {  'name': 'BC_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'B' ],
                         'verifyHostname': 'no',
                         'cost' : cls.cost['BC'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    )
                 ]
               )


        router ( 'D',
                 [
                    ( 'listener',
                      { 'port': client_ports['D'],
                        'role': 'normal',
                        'stripAnnotations': 'no',
                        'linkCapacity' : client_link_capacity
                      }
                    ),
                    ( 'connector',
                      {  'name': 'AD_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'A' ],
                         'verifyHostname': 'no',
                         'cost' : cls.cost['AD'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    ),
                    ( 'connector',
                      {  'name': 'AD2_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'A' ],
                         'verifyHostname': 'no',
                         'cost' : cls.cost['AD2'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    ),
                    ( 'connector',
                      {  'name': 'BD_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'B' ],
                         'verifyHostname': 'no',
                         'cost' : cls.cost['BD'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    ),
                    ( 'connector',
                      {  'name': 'CD_connector',
                         'role': 'inter-router',
                         'port': inter_router_ports [ 'C' ],
                         'verifyHostname': 'no',
                         'cost' : cls.cost['CD'],
                         'stripAnnotations': 'no',
                         'linkCapacity' : inter_router_link_capacity
                      }
                    )
                 ]
               )


        router_A = cls.routers[0]
        router_B = cls.routers[1]
        router_C = cls.routers[2]
        router_D = cls.routers[3]

        router_A.wait_router_connected('B')
        router_A.wait_router_connected('C')
        router_A.wait_router_connected('D')

        cls.client_addrs = dict()
        cls.client_addrs['A'] = router_A.addresses[0]
        cls.client_addrs['B'] = router_B.addresses[0]
        cls.client_addrs['C'] = router_C.addresses[0]
        cls.client_addrs['D'] = router_D.addresses[0]

        # 1 means skip that test.
        cls.skip = { 'test_01' : 0,
                     'test_02' : 0
                   }


    def test_01_delete_spurious_connector ( self ):
        name = 'test_01'
        if self.skip [ name ] :
            self.skipTest ( "Test skipped during development." )
        test = DeleteSpuriousConnector ( name,
                                         self.client_addrs,
                                         'closest/01',
                                         self.client_addrs['D']
                                       )
        test.run()
        self.assertEqual ( None, test.error )


    def test_02_topology_disposition ( self ):
        name = 'test_02'
        if self.skip [ name ] :
            self.skipTest ( "Test skipped during development." )
        test = TopologyDisposition ( name,
                                     self.client_addrs,
                                     "closest/02"
                                   )
        test.run()
        self.assertEqual ( None, test.error )



#################################################################
#     Tests
#################################################################


class DeleteSpuriousConnector ( MessagingHandler ):
    """
    """
    def __init__ ( self, test_name, client_addrs, destination, D_client_addr ):
        super ( DeleteSpuriousConnector, self).__init__(prefetch=100)
        self.test_name           = test_name
        self.client_addrs        = client_addrs
        self.D_client_addr       = D_client_addr
        self.dest                = destination
        self.error               = None
        self.sender              = None
        self.receiver            = None
        self.debug               = False

        self.n_messages          = 30
        self.n_received          = 0
        self.n_accepted          = 0
        self.n_released          = 0
        self.n_sent              = 0

        self.burst_size          = 3
        self.timers              = dict()
        self.reactor             = None
        self.bailing             = False
        self.sender_connection   = None
        self.receiver_connection = None
        self.connections         = []

        self.D_management_connection = None
        self.D_management_receiver   = None
        self.D_management_sender     = None
        self.D_management_helper     = None

        self.confirmed_kill = False


    def debug_print ( self, text ) :
        if self.debug == True:
            print "%.6lf %s" % ( time.time(), text )


    # Shut down everything and exit.
    def bail ( self, text ):
        self.bailing = True
        self.error = text

        for stopwatch in self.timers.values() :
            stopwatch.timer.cancel()

        for cnx in self.connections :
            cnx.close ( )


    # Call this from all handlers of dispositions returning to the sender.
    def bail_out_if_done ( self ) :
        if self.n_accepted + self.n_released >= self.n_messages  :
            # We have received everything. But! Did we get a confirmed kill on the connector?
            if not self.confirmed_kill :
                # This is a failure.
                self.bail ( "No confirmed kill on connector." )
            else :
                # Success!
                self.bail ( None )


    def timeout ( self, timer_name ):
        # If we are in the process of punching out, ignore all other timers.
        if self.bailing :
            return

        self.debug_print ( "timeout %s" % timer_name )

        # If this is the doomsday timer, just punch out.
        if timer_name == 'test' :
            self.bail ( None )
            return

        # Timer-specific actions.
        if timer_name == 'sender' :
            self.send ( )

        # Generic actions for all timers.
        stopwatch = self.timers[timer_name]
        stopwatch.timer = self.reactor.schedule ( stopwatch.repeat_time, Timeout(self, timer_name) )


    def on_start ( self, event ):
        self.reactor = event.reactor

        # This stopwatch will end the test.
        stopwatch_name='test'
        init_time=60
        self.timers[stopwatch_name] = \
            Stopwatch ( name = stopwatch_name, \
                        timer = event.reactor.schedule(init_time, Timeout(self, stopwatch_name)), \
                        initial_time = init_time, \
                        repeat_time = 0 \
                      )

        # This stopwatch calls the sender.
        stopwatch_name='sender'
        init_time=2
        self.timers[stopwatch_name] = \
            Stopwatch ( name = stopwatch_name, \
                        timer = event.reactor.schedule(init_time, Timeout(self, stopwatch_name)), \
                        initial_time = init_time, \
                        repeat_time = 0.5 \
                      )

        self.sender_connection   = event.container.connect ( self.client_addrs['A'] )
        self.receiver_connection = event.container.connect ( self.client_addrs['B'] )
        self.connections.append ( self.sender_connection )
        self.connections.append ( self.receiver_connection )

        self.sender   = event.container.create_sender   ( self.sender_connection,   self.dest, name='sender' )
        self.receiver = event.container.create_receiver ( self.receiver_connection, self.dest, name='receiver' )

        # In this test, we send a single management command to the D router
        # to kill a 'spurious', i.e. unused, connector.
        self.D_management_connection = event.container.connect ( self.D_client_addr )
        self.D_management_receiver   = event.container.create_receiver ( self.D_management_connection, dynamic=True )
        self.D_management_sender     = event.container.create_sender   ( self.D_management_connection, "$management" )
        self.connections.append ( self.D_management_connection )


    def on_link_opened ( self, event ) :
        self.debug_print ( "on_link_opened" )
        if event.receiver:
            event.receiver.flow ( self.n_messages )

        if event.receiver == self.D_management_receiver :
            event.receiver.flow ( 100 )
            self.D_management_helper = ManagementMessageHelper ( event.receiver.remote_source.address )


    def on_released ( self, event ) :
        # The test fails.
        self.bail ( "a message was released" )


    def run(self):
        Container(self).run()


    def kill_the_connector ( self ) :
        router = 'D'
        connector = 'AD2_connector'
        self.debug_print ( "killing connector %s on router %s" % (connector, router) )
        msg = self.D_management_helper.make_connector_delete_command ( connector )
        self.debug_print ( "killing connector %s on router %s" % (connector, router) )
        self.D_management_sender.send ( msg )
        self.most_recent_kill = time.time()


    def parse_link_query_response ( self, msg ) :
        if msg.properties :
           if "statusDescription" in msg.properties and "statusCode" in msg.properties :
               if msg.properties["statusDescription"] == "No Content" and msg.properties["statusCode"] == 204 :
                   self.debug_print ( "AD2_connector was killed" )
                   self.confirmed_kill = True


    #=======================================================
    # Sender Side
    #=======================================================

    def send ( self ) :

        if self.n_sent >= self.n_messages :
            return

        for _ in xrange ( self.burst_size ) :
            if self.sender.credit > 0 and self.n_sent < self.n_messages :
                msg = Message ( body=self.n_sent )
                self.n_sent += 1
                self.sender.send ( msg )
                self.debug_print ( "sent: %d" % self.n_sent )
            else :
                self.debug_print ( "sender has no credit." )


    def on_accepted ( self, event ):
        self.n_accepted += 1
        self.debug_print ( "on_accepted %d" % self.n_accepted )
        self.bail_out_if_done ( )


    def on_released ( self, event ) :
        self.n_released += 1
        self.debug_print ( "on_released %d" % self.n_released )
        self.bail_out_if_done ( )


    #=======================================================
    # Receiver Side
    #=======================================================

    def on_message ( self, event ):
        if event.receiver == self.D_management_receiver :
            self.parse_link_query_response ( event.message )
        else :
            self.n_received += 1
            self.debug_print ( "received message %s" % event.message.body )
            self.debug_print ( "n_received == %d" % self.n_received )
            if self.n_received == 13 :
                self.kill_the_connector ( )





class TopologyDisposition ( MessagingHandler ):
    """
    Test that disposition guarantee survives catastrophic
    damage to the router network.
    """
    #  TopologyDisposition Notes
    #  ========================================
    #
    #     1. What is the goal of this test?
    #     ------------------------------------------
    #       The point of this test is to make sure that, in spite of
    #       serious disruption to a complex router network topology,
    #       the sender always knows the dispositions of its messages.
    #       By the end of the test, it should know that all sent
    #       messages were either received or released.
    #       ( Note that some messages may be "modified", but the reactor
    #       interface that this test uses issues on_released events for
    #       modified messages, same as released, so I am lumping them
    #       together.
    #
    #
    #     2. Routes and Connector Kills
    #     ------------------------------------------
    #       Messages are always sent from A, and received at B.
    #       Routes are controlled by assigning different costs to the
    #       various links, and by then killing 3 connectors one at a timee,
    #       at different points in the test.
    #         First route ahould be ADCB.
    #         Then we kill connector  CD.
    #         Next route should be   ADB.
    #         Then we kill connector  BD.
    #         Next route should be   ACB.
    #         Then we kill connector  BC.
    #         Final route should be   AB.
    #
    #
    #     3. Two Timers
    #     ------------------------------------------
    #     Sending is done in batches, using a timer. The timer expires
    #     once every 0.5 seconds, and we send a small batch of 10 messages
    #     (or as many as the sender has credit for).
    #     There is also a deadline timer that terminates the test with
    #     failure if it ever fires.
    #
    #
    #     4. The Simple State Machine
    #     ------------------------------------------
    #     I want behavior that is a little more complex than what would
    #     be possible by simply reacting to the callback functions, so I
    #     impose on top of them a simple state machine. The states proceed
    #     in a simple linear sequence, and some of the callback functions
    #     consult the current state befoe deciding what they should do.
    #     And bump the state machine to its next state when appropriate.
    #
    #                state            purpose
    #                ----------------------------------------
    #
    #                starting         placeholder
    #
    #                topo checking    make sure that the 4 routers are
    #                                 completely connected, as expected.
    #
    #                link checking    visual inspection of various link data
    #                                 during debugging.
    #
    #                sending          send the messages, in 70 batches of 10,
    #                                 spaced 0.5 seconds apart.
    #
    #                done sending     quite sending messages and wait for either
    #                                 the sum of ACCEPTED + RELEASED to add up to
    #                                 SENT, causing the test to succeed, or the
    #                                 test timer to expire, causing the test to fail.
    #
    #                bailing          enter this state when we are in the process of
    #                                 exiting. All callbacks should take no action if
    #                                 the test has entered this state.
    #
    #
    #     5. Sending in bursts.
    #     ----------------------------------
    #     When the send-timer goes off, I send a burst of messages ( self.send_burst_size ).
    #     There is no especially great reason for this, except that I liked the idea of a
    #     send timer because it seemed more realistic to me -- more like a real application --
    #     and that implies sending bursts of messages.


    def __init__ ( self, test_name, client_addrs, destination ):
        super(TopologyDisposition, self).__init__(prefetch=10)
        self.dest                 = destination
        self.error                = None
        self.sender               = None
        self.receiver             = None
        self.test_timer           = None
        self.send_timer           = None
        self.n_sent               = 0
        self.n_accepted           = 0
        self.n_received           = 0
        self.n_released           = 0
        self.reactor              = None
        self.state                = None
        self.send_conn            = None
        self.recv_conn            = None
        self.debug                = False
        self.client_addrs         = client_addrs
        self.timeout_count        = 0
        self.confirmed_kills      = 0
        self.send_interval        = 0.5
        self.to_be_sent           = 700
        self.deadline             = 100
        self.message_status       = dict()
        self.message_times        = dict()
        self.most_recent_kill     = 0
        self.first_trouble        = 0
        self.flow                 = 100
        self.max_trouble_duration = 20
        self.link_check_count     = 0
        self.send_burst_size      = 10

        # Holds the management sender, receiver, and 'helper'
        # associated with each router.
        self.routers = {
                         'A' : dict(),
                         'B' : dict(),
                         'C' : dict(),
                         'D' : dict()
                       }

        # This tells the system in what order to kill the connectors.
        self.kill_count = 0
        self.kill_list = (
                           ( 'D', 'CD_connector' ),
                           ( 'D', 'BD_connector' ),
                           ( 'C', 'BC_connector' )
                         )

        # We use this to keep track of which connectors we have found
        # when the test is first getting started and we are checking
        # the topology.
        self.connectors_map = { 'AB_connector' : 0,
                                'AC_connector' : 0,
                                'AD_connector' : 0,
                                'BC_connector' : 0,
                                'BD_connector' : 0,
                                'CD_connector' : 0
                              }


    def state_transition ( self, message, new_state ) :
        if self.state == new_state :
            return
        self.state = new_state
        self.debug_print ( "state transition to : %s -- because %s" % ( self.state, message ) )


    def debug_print ( self, text ) :
        if self.debug == True:
            print "%.6lf %s" % ( time.time(), text )


    # Shut down everything and exit.
    def bail ( self, text ):
        self.state = 'bailing'
        self.test_timer.cancel ( )
        self.send_timer.cancel ( )

        self.error = text

        self.send_conn.close ( )
        self.recv_conn.close ( )

        self.routers['A'] ['mgmt_conn'].close()
        self.routers['B'] ['mgmt_conn'].close()
        self.routers['C'] ['mgmt_conn'].close()
        self.routers['D'] ['mgmt_conn'].close()


    # Two separate timers. One controls sending in bursts, one ends the test.
    def timeout ( self, name ):
        if self.state == 'bailing' :
            return

        self.timeout_count += 1
        if name == 'test':
            self.state_transition ( 'Timeout Expired', 'bailing' )
            self.bail ( "Timeout Expired: n_sent=%d n_released=%d n_accepted=%d" % \
                         ( self.n_sent, self.n_released, self.n_accepted ) )
            return
        elif name == 'sender':
            if self.state == 'sending' :
                if not (self.timeout_count % 20):
                    if self.kill_count < len(self.kill_list):
                        self.kill_a_connector ( self.kill_list[self.kill_count] )
                        self.kill_count += 1
                self.send ( )
                if self.n_sent >= self.to_be_sent :
                    self.state_transition ( 'sent %d messages' % self.to_be_sent, 'done sending' )
            elif self.state == 'done sending' :
                if self.n_sent == self.n_accepted + self.n_released :
                    self.state_transition ( 'success', 'bailing' )
                    self.bail ( None )

            self.debug_print ( "sent: %d  received: %d accepted: %d   released: %d  confirmed kills: %d" % \
                ( self.n_sent, self.n_received, self.n_accepted, self.n_released, self.confirmed_kills ) )

            diff = self.n_sent - (self.n_accepted + self.n_released)

            # If the difference between n_sent and (accepted + released) is
            # ever greater than 10 (the send batch size)
            if diff >= self.send_burst_size and self.state == 'done sending' :
                self.debug_print ( "TROUBLE : %d" % diff )

                if self.first_trouble == 0:
                    self.first_trouble = time.time()
                    self.debug_print ( "first trouble at %.6lf" % self.first_trouble )
                else:
                    trouble_duration = time.time() - self.first_trouble
                    self.debug_print ( "trouble duration %.6lf" % trouble_duration )
                    if trouble_duration >= self.max_trouble_duration :
                        self.state_transition ( 'trouble duration exceeded limit: %d' % self.max_trouble_duration, 'post mortem' )
                        self.check_links ( )

            self.send_timer = self.reactor.schedule ( self.send_interval, Timeout(self, "sender") )



    def on_start ( self, event ):
        self.state_transition ( 'on_start', 'starting' )
        self.reactor = event.reactor
        self.test_timer = event.reactor.schedule ( self.deadline, Timeout(self, "test") )
        self.send_timer = event.reactor.schedule ( self.send_interval, Timeout(self, "sender") )
        self.send_conn  = event.container.connect ( self.client_addrs['A'] )
        self.recv_conn  = event.container.connect ( self.client_addrs['B'] )

        self.sender     = event.container.create_sender   ( self.send_conn, self.dest )
        self.receiver   = event.container.create_receiver ( self.recv_conn, self.dest )

        self.routers['A'] ['mgmt_conn'] = event.container.connect ( self.client_addrs['A'] )
        self.routers['B'] ['mgmt_conn'] = event.container.connect ( self.client_addrs['B'] )
        self.routers['C'] ['mgmt_conn'] = event.container.connect ( self.client_addrs['C'] )
        self.routers['D'] ['mgmt_conn'] = event.container.connect ( self.client_addrs['D'] )

        self.routers['A'] ['mgmt_receiver'] = event.container.create_receiver ( self.routers['A'] ['mgmt_conn'], dynamic=True )
        self.routers['B'] ['mgmt_receiver'] = event.container.create_receiver ( self.routers['B'] ['mgmt_conn'], dynamic=True )
        self.routers['C'] ['mgmt_receiver'] = event.container.create_receiver ( self.routers['C'] ['mgmt_conn'], dynamic=True )
        self.routers['D'] ['mgmt_receiver'] = event.container.create_receiver ( self.routers['D'] ['mgmt_conn'], dynamic=True )

        self.routers['A'] ['mgmt_sender']   = event.container.create_sender   ( self.routers['A'] ['mgmt_conn'], "$management" )
        self.routers['B'] ['mgmt_sender']   = event.container.create_sender   ( self.routers['B'] ['mgmt_conn'], "$management" )
        self.routers['C'] ['mgmt_sender']   = event.container.create_sender   ( self.routers['C'] ['mgmt_conn'], "$management" )
        self.routers['D'] ['mgmt_sender']   = event.container.create_sender   ( self.routers['D'] ['mgmt_conn'], "$management" )



    #-----------------------------------------------------------------
    # At start-time, as the management links to the routers open,
    # check each one to make sure that it has all the expected
    # connections.
    #-----------------------------------------------------------------
    def on_link_opened ( self, event ) :
        self.state_transition ( 'on_link_opened', 'topo checking' )
        # The A mgmt link has opened.  --------------------------
        # Give it some credit, but we don't need to use this one until
        # later, if there is a problem.
        if event.receiver == self.routers['A'] ['mgmt_receiver'] :
            event.receiver.flow ( self.flow )
            self.routers['A'] ['mgmt_helper'] = ManagementMessageHelper ( event.receiver.remote_source.address )
        # The B mgmt link has opened. Check its connections. --------------------------
        elif event.receiver == self.routers['B'] ['mgmt_receiver'] :
            event.receiver.flow ( self.flow )
            self.routers['B'] ['mgmt_helper'] = ManagementMessageHelper ( event.receiver.remote_source.address )
            for connector in [ 'AB_connector' ] :
                self.connector_check ( 'B', connector )
        # The C mgmt link has opened. Check its connections. --------------------------
        elif event.receiver == self.routers['C'] ['mgmt_receiver'] :
            event.receiver.flow ( self.flow )
            self.routers['C'] ['mgmt_helper'] = ManagementMessageHelper ( event.receiver.remote_source.address )
            for connector in [ 'AC_connector', 'BC_connector' ] :
                self.connector_check ( 'C', connector )
        # The D mgmt link has opened. Check its connections. --------------------------
        elif event.receiver == self.routers['D'] ['mgmt_receiver']:
            event.receiver.flow ( self.flow )
            self.routers['D'] ['mgmt_helper'] = ManagementMessageHelper ( event.receiver.remote_source.address )
            for connector in [ 'AD_connector', 'BD_connector', 'CD_connector' ] :
                self.connector_check ( 'D', connector )


    def send ( self ):
        if self.state != 'sending' :
            self.debug_print ( "send called while state is %s" % self.state )
            return

        for _ in xrange ( self.send_burst_size ) :
            if self.sender.credit > 0 :
                msg = Message ( body=self.n_sent )
                msg_tag=str(self.n_sent)
                dlv = self.sender.send ( msg, tag = msg_tag )
                if dlv == None :
                    self.debug_print ( "send failed" )
                    return
                self.message_status [ msg_tag ] = 'sent'
                self.message_times  [ msg_tag ] = time.time()
                self.n_sent += 1
        self.debug_print ( "send: n_sent %d credit is now: %d" % ( self.n_sent, self.sender.credit ) )


    def on_message ( self, event ):
        #----------------------------------------------------------------
        # Is this a management message?
        #----------------------------------------------------------------
        if event.receiver == self.routers['A'] ['mgmt_receiver'] or \
           event.receiver == self.routers['B'] ['mgmt_receiver'] or \
           event.receiver == self.routers['C'] ['mgmt_receiver'] or \
           event.receiver == self.routers['D'] ['mgmt_receiver'] :

            if self.state == 'topo checking' :
                # In the 'topo checking' state, we send management messages to
                # ask the 4 routers about their connections. Then, parsing the
                # replies, we make sure that we count the expected 6 connections.
                # (The 4 routers are completely connected.)
                if 'OK' == event.message.properties['statusDescription']:
                    connection_name = event.message.body['name']

                    if connection_name in self.connectors_map :
                        self.connectors_map [ connection_name ] = 1
                        self.debug_print ( "topo check found connector %s" % connection_name )
                    else :
                      self.bail ( "bad connection name: %s" % connection_name )

                    n_connections = sum(self.connectors_map.values())
                    if n_connections == 6 :
                      self.state_transition ( 'topo check successful', 'link checking' )
                      self.check_links ( )

            elif self.state == 'link checking' or self.state == 'post mortem' :
                # Link checking was used during initial debugging of this test,
                # to visually check on the number of undelivered and unsettled
                # messages in each link, especially during the "post mortem"
                # state triggered by a failure.
                if   event.receiver == self.routers['A'] ['mgmt_receiver'] :
                    self.debug_print ( "received link check message from A ------------" )
                elif event.receiver == self.routers['B'] ['mgmt_receiver'] :
                    self.debug_print ( "received link check message from B ------------" )
                elif event.receiver == self.routers['C'] ['mgmt_receiver'] :
                    self.debug_print ( "received link check message from C ------------" )
                elif event.receiver == self.routers['D'] ['mgmt_receiver'] :
                    self.debug_print ( "received link check message from D ------------" )
                body = event.message.body
                self.debug_print ( "body: %s" % body )
                self.debug_print ( "properties: %s" % event.message.properties )

                self.link_check_count -= 1
                if self.link_check_count == 0 :
                    if self.state == 'link checking' :
                        self.state_transition ( 'link check successful', 'sending' )
                        self.send()
                    elif self.state == 'post mortem' :
                        self.state_transition ( "post mortem complete", 'bailing' )
                        self.bail ( "failed" )
            elif self.state == 'sending' :
                if 'No Content' ==  event.message.properties['statusDescription']:
                    self.confirmed_kills += 1

        else :
            if event.receiver == self.receiver :
                self.n_received += 1



    def on_accepted ( self, event ):
        if event.sender == self.sender:
            self.n_accepted += 1
            tag = event.delivery.tag
            self.message_status [ tag ] = 'accepted'


    def on_released ( self, event ) :

        if event.sender == self.sender:
            self.n_released += 1
            tag = event.delivery.tag
            self.message_status [ tag ] = 'released'


    def connector_check ( self, router, connector ) :
        self.debug_print ( "checking connector %s for router %s" % (connector, router) )
        mgmt_helper = self.routers[router] ['mgmt_helper']
        mgmt_sender = self.routers[router] ['mgmt_sender']
        msg = mgmt_helper.make_connector_query ( connector )
        mgmt_sender.send ( msg )


    def check_links ( self ) :
        self.link_check_count = 4
        self.link_check ( 'A' )
        self.link_check ( 'B' )
        self.link_check ( 'C' )
        self.link_check ( 'D' )


    def link_check ( self, router_name ) :
        mgmt_helper = self.routers[router_name] ['mgmt_helper']
        mgmt_sender = self.routers[router_name] ['mgmt_sender']
        msg = mgmt_helper.make_router_link_query ( )
        mgmt_sender.send ( msg )


    # The target structure provides the name of the router and the name of its connector
    # that is to be killed. Create the appropriate management message, and send it off.
    def kill_a_connector ( self, target ) :
        router = target[0]
        connector = target[1]
        mgmt_helper = self.routers[router] ['mgmt_helper']
        mgmt_sender = self.routers[router] ['mgmt_sender']
        msg = mgmt_helper.make_connector_delete_command ( connector )
        self.debug_print ( "killing connector %s on router %s" % (connector, router) )
        mgmt_sender.send ( msg )
        self.most_recent_kill = time.time()


    # Used during debugging.
    def print_message_status ( self ) :
        for i in range ( self.n_sent ) :
            tag = str ( i )
            print tag, self.message_status [ tag ]


    # Used during debugging.
    def print_unknown_messages ( self ) :
        count = 0
        print "Messages with unknown status: "
        for i in range ( self.n_sent ) :
            tag = str ( i )
            if self.message_status [ tag ] == 'sent' :
              count = count + 1
              print '    ', tag, 'sent:', self.message_times [ tag ]
        print "    total: ", count


    # Used during debugging.
    def quick_print_unknown_messages ( self ) :
        count = 0
        print "Messages with unknown status: "

        first = -1
        last  =  0

        for i in range ( self.n_sent ) :
            tag = str ( i )
            if self.message_status [ tag ] == 'sent' : # It's not accepted or released.
              count = count + 1
              if first == -1 :
                first = i
              if i > last :
                last = i

        print '    first : ', first, 'sent : %.6lf' % self.message_times [ str(first) ]
        print '    last  : ', last,  'sent : %.6lf' % self.message_times [ str(last)  ]
        print "    total : ", count


    def run(self):
        Container(self).run()





if __name__ == '__main__':
    unittest.main(main_module())