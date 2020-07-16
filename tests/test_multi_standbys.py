import pgautofailover_utils as pgautofailover
from nose.tools import *
import time

import os.path

cluster = None
monitor = None
node1 = None
node2 = None
node3 = None
node4 = None

def setup_module():
    global cluster
    cluster = pgautofailover.Cluster()

def teardown_module():
    cluster.destroy()

def test_000_create_monitor():
    global monitor
    monitor = cluster.create_monitor("/tmp/multi_standby/monitor")
    monitor.run()
    monitor.wait_until_pg_is_running()
    monitor.run()

def test_001_init_primary():
    global node1
    node1 = cluster.create_datanode("/tmp/multi_standby/node1")
    node1.create()
    node1.run()
    assert node1.wait_until_state(target_state="single")

def test_002_candidate_priority():
    assert node1.get_candidate_priority() == 100

    assert not node1.set_candidate_priority(-1)
    assert node1.get_candidate_priority() == 100

    assert node1.set_candidate_priority(99)
    assert node1.get_candidate_priority() == 99

def test_003_replication_quorum():
    assert node1.get_replication_quorum()

    assert not node1.set_replication_quorum("wrong quorum")
    assert node1.get_replication_quorum()

    assert node1.set_replication_quorum("false")
    assert not node1.get_replication_quorum()

    assert node1.set_replication_quorum("true")
    assert node1.get_replication_quorum()

def test_004_add_three_standbys():
    # the next test wants to set number_sync_standbys to 2
    # so we need at least 3 standbys to allow that
    global node2
    global node3
    global node4

    node2 = cluster.create_datanode("/tmp/multi_standby/node2")
    node2.create()
    node2.run()
    assert node2.wait_until_state(target_state="secondary")

    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()

    # refrain from waiting for the primary to be ready, to trigger a race
    # condition that could segfault the monitor (if the code was less
    # careful than it is now)
    # assert node1.wait_until_state(target_state="primary")

    node3 = cluster.create_datanode("/tmp/multi_standby/node3")
    node3.create()
    node3.run()
    assert node3.wait_until_state(target_state="secondary")
    assert node1.wait_until_state(target_state="primary")

    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()
    assert node3.has_needed_replication_slots()

    node4 = cluster.create_datanode("/tmp/multi_standby/node4")
    node4.create()
    node4.run()
    assert node4.wait_until_state(target_state="secondary")

    # make sure we reached primary on node1 before next tests
    assert node1.wait_until_state(target_state="primary")

    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()
    assert node3.has_needed_replication_slots()
    assert node4.has_needed_replication_slots()

def test_005_number_sync_standbys():
    print()
    assert node1.get_number_sync_standbys() == 1
    assert not node1.set_number_sync_standbys(-1)
    assert node1.get_number_sync_standbys() == 1

    print("set number_sync_standbys = 2")
    assert node1.set_number_sync_standbys(2)
    assert node1.get_number_sync_standbys() == 2
    print("synchronous_standby_names = '%s'" %
          node1.get_synchronous_standby_names())

    print("set number_sync_standbys = 0")
    assert node1.set_number_sync_standbys(0)
    assert node1.get_number_sync_standbys() == 0
    print("synchronous_standby_names = '%s'" %
          node1.get_synchronous_standby_names())

    print("set number_sync_standbys = 1")
    assert node1.set_number_sync_standbys(1)
    assert node1.get_number_sync_standbys() == 1
    print("synchronous_standby_names = '%s'" %
          node1.get_synchronous_standby_names())

def test_006_number_sync_standbys_trigger():
    assert node1.set_number_sync_standbys(2)
    assert node1.get_number_sync_standbys() == 2

    node4.drop()
    assert node1.get_number_sync_standbys() == 1
    assert node1.wait_until_state(target_state="primary")

    # there's no state change to instruct us that the replication slot
    # maintenance is now done, so we have to wait for awhile instead.
    # pg_autoctl connects to the monitor every 5s, so let's sleep 6s
    time.sleep(6)
    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()
    assert node3.has_needed_replication_slots()

def test_007_create_t1():
    node1.run_sql_query("CREATE TABLE t1(a int)")
    node1.run_sql_query("INSERT INTO t1 VALUES (1), (2)")
    node1.run_sql_query("CHECKPOINT")

@raises(Exception)
def test_008a_set_candidate_priorities_to_zero():
    # we need two nodes with non-zero candidate priority
    node1.set_candidate_priority(0)
    node2.set_candidate_priority(0)

def test_008b_set_candidate_priorities():
    # set priorities in a way that we know the candidate: node2
    node1.set_candidate_priority(90) # current primary
    node2.set_candidate_priority(90)
    node3.set_candidate_priority(70)

    # when we set candidate priority we go to join_primary then primary
    print()
    assert node1.wait_until_state(target_state="primary")

def test_009_failover():
    print()
    print("Calling pgautofailover.failover() on the monitor")
    monitor.failover()
    assert node2.wait_until_state(target_state="primary")
    assert node3.wait_until_state(target_state="secondary")
    assert node1.wait_until_state(target_state="secondary")

    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()
    assert node3.has_needed_replication_slots()

def test_010_read_from_nodes():
    assert node1.run_sql_query("SELECT * FROM t1") == [(1,), (2,)]
    assert node2.run_sql_query("SELECT * FROM t1") == [(1,), (2,)]
    assert node3.run_sql_query("SELECT * FROM t1") == [(1,), (2,)]

def test_011_write_into_new_primary():
    node2.run_sql_query("INSERT INTO t1 VALUES (3), (4)")
    results = node2.run_sql_query("SELECT * FROM t1")
    assert results == [(1,), (2,), (3,), (4,)]

    # generate more WAL trafic for replication
    node2.run_sql_query("CHECKPOINT")

def test_012_set_candidate_priorities():
    print()
    assert node2.wait_until_state(target_state="primary")

    # set priorities in a way that we know the candidate: node3
    node1.set_candidate_priority(70)
    node2.set_candidate_priority(90) # current primary
    node3.set_candidate_priority(90)

    # when we set candidate priority we go to apply_settings then primary
    print()
    assert node2.wait_until_state(target_state="primary")

def test_013_maintenance_and_failover():
    print()
    print("Enabling maintenance on node1")
    node1.enable_maintenance()
    assert node1.wait_until_state(target_state="maintenance")
    node1.stop_postgres()

    # assigned and goal state must be the same
    assert node2.wait_until_state(target_state="join_primary")

    print("Calling pgautofailover.failover() on the monitor")
    monitor.failover()
    assert node3.wait_until_state(target_state="primary")
    assert node2.wait_until_state(target_state="secondary")

    print("Disabling maintenance on node1, should connect to the new primary")
    node1.disable_maintenance()

    # allow manual checking of primary_conninfo
    print("current primary is node3 at %s" % str(node3.vnode.address))

    if node1.pgmajor() < 12:
        fn = "recovery.conf"
    else:
        fn = "postgresql-auto-failover-standby.conf"

    fn = os.path.join(node1.datadir, fn)
    print("%s:\n%s" % (fn, open(fn).read()))

    assert node1.wait_until_state(target_state="secondary")

    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()
    assert node3.has_needed_replication_slots()

def test_014_read_from_new_primary():
    results = node3.run_sql_query("SELECT * FROM t1")
    assert results == [(1,), (2,), (3,), (4,)]

def test_015_set_candidate_priorities():
    print()
    assert node3.wait_until_state(target_state="primary")

    node1.set_candidate_priority(90) # secondary
    node2.set_candidate_priority(0)  # not a candidate anymore
    node3.set_candidate_priority(90) # current primary

    # when we set candidate priority we go to apply_settings then primary
    print()
    assert node3.wait_until_state(target_state="primary")

def test_016_ifdown_node1():
    node1.ifdown()

def test_017_insert_rows():
    node3.run_sql_query(
        "INSERT INTO t1 SELECT x+10 FROM generate_series(1, 100000) as gs(x)")
    node3.run_sql_query("CHECKPOINT")

    lsn = node3.run_sql_query("select pg_current_wal_lsn()")[0][0]
    print("%s " % lsn, end="", flush=True)

def test_018_failover():
    print()
    print("Injecting failure of node3")
    node3.fail()

    # have node2 re-join the network and hopefully reconnect etc
    print("Reconnecting node1 (ifconfig up)")
    node1.ifup()

    # now we should be able to continue with the failover, and fetch missing
    # WAL bits from node2
    node1.wait_until_pg_is_running()
    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()

    assert node1.wait_until_state(target_state="wait_primary", timeout=120)
    assert node2.wait_until_state(target_state="secondary")
    assert node1.wait_until_state(target_state="primary")

def test_019_read_from_new_primary():
    results = node1.run_sql_query("SELECT count(*) FROM t1")
    assert results == [(100004,)]

def test_020_start_node3_again():
    node3.run()
    assert node3.wait_until_state(target_state="secondary")

    assert node1.wait_until_state(target_state="primary")
    assert node2.wait_until_state(target_state="secondary")

    assert node1.has_needed_replication_slots()
    assert node2.has_needed_replication_slots()
    assert node3.has_needed_replication_slots()
