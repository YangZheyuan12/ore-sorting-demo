import unittest

from sorting_demo.classifier_adapter import ConservativeDecisionPolicy, demo_batch
from sorting_demo.domain import ErrorCode, MachineState, MaterialClass, SortItem, SortingMachine


class SortingMachineTests(unittest.TestCase):
    def test_standard_five_item_batch_keeps_fifo_order(self) -> None:
        machine = SortingMachine()
        items = demo_batch()
        machine.load_batch(items)
        machine.start()
        routed = []
        now = 0.0
        for item in items:
            self.assertTrue(machine.trigger_entry(now))
            action = machine.trigger_exit(now + 1.0)
            self.assertIsNotNone(action)
            routed.append((action.item.item_id, action.item.label))
            now += 2.5
        self.assertEqual(routed, [(item.item_id, item.label) for item in items])
        self.assertEqual(machine.state, MachineState.COMPLETE)
        self.assertEqual(machine.ore_count, 3)
        self.assertEqual(machine.waste_count, 2)

    def test_extra_object_stops_machine(self) -> None:
        machine = SortingMachine()
        machine.load_batch([SortItem("A", MaterialClass.ORE)])
        machine.start()
        machine.trigger_entry(0.0)
        machine.trigger_entry(0.3)
        self.assertEqual(machine.state, MachineState.ERROR)
        self.assertEqual(machine.error, ErrorCode.EXTRA_OBJECT)
        self.assertEqual(machine.servo_target, MaterialClass.ORE)

    def test_empty_queue_exit_stops_machine(self) -> None:
        machine = SortingMachine()
        machine.start_diagnostic_run()
        machine.trigger_exit(0.0)
        self.assertEqual(machine.error, ErrorCode.QUEUE_EMPTY)

    def test_exit_cannot_lead_entry(self) -> None:
        machine = SortingMachine()
        machine.load_batch([SortItem("A", MaterialClass.WASTE)])
        machine.start()
        machine.trigger_exit(0.0)
        self.assertEqual(machine.error, ErrorCode.SEQUENCE_ERROR)

    def test_sensor_recovery_filters_bounce(self) -> None:
        machine = SortingMachine(sensor_recovery_seconds=0.2)
        machine.load_batch([
            SortItem("A", MaterialClass.ORE),
            SortItem("B", MaterialClass.ORE),
        ])
        machine.start()
        self.assertTrue(machine.trigger_entry(0.0))
        self.assertFalse(machine.trigger_entry(0.03))
        self.assertEqual(machine.in_count, 1)
        self.assertTrue(machine.trigger_entry(0.21))

    def test_conservative_policy_defaults_uncertain_sample_to_ore(self) -> None:
        policy = ConservativeDecisionPolicy(waste_threshold=0.72)
        self.assertEqual(policy.decide(0.60).label, MaterialClass.ORE)
        self.assertEqual(policy.decide(0.80).label, MaterialClass.WASTE)


if __name__ == "__main__":
    unittest.main()
