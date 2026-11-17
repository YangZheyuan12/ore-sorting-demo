import unittest

from sorting_demo.classifier_adapter import demo_batch
from sorting_demo.domain import MaterialClass, SortItem
from sorting_demo.protocol import (
    HARDWARE_BAUDRATE,
    HARDWARE_STOP_COMMAND,
    batch_commands,
    parse_line,
    validate_device_message,
)


class ProtocolTests(unittest.TestCase):
    def test_batch_commands_are_deterministic(self) -> None:
        commands = batch_commands(demo_batch())
        self.assertEqual(
            commands,
            ["BATCH 5", "ORE", "WASTE", "ORE", "WASTE", "ORE", "END"],
        )
        self.assertEqual(HARDWARE_BAUDRATE, 9600)
        self.assertEqual(HARDWARE_STOP_COMMAND, "BATCH 0")

    def test_batch_commands_support_variable_image_counts(self) -> None:
        for count in (1, 3, 20):
            items = [SortItem(f"P{index:02d}", MaterialClass.ORE) for index in range(1, count + 1)]
            commands = batch_commands(items)
            self.assertEqual(commands[0], f"BATCH {count}")
            self.assertEqual(commands[-1], "END")
            self.assertEqual(len(commands), count + 2)

    def test_parse_device_event(self) -> None:
        message = parse_line("OUT 2 WASTE\r\n")
        self.assertEqual(message.kind, "OUT")
        self.assertEqual(message.fields, ("2", "WASTE"))
        self.assertTrue(validate_device_message(message))

    def test_parse_error_with_colon(self) -> None:
        message = parse_line("ERROR: NOT ARMED\r\n")
        self.assertEqual(message.kind, "ERROR")
        self.assertEqual(message.fields, ("NOT", "ARMED"))
        self.assertTrue(validate_device_message(message))

    def test_parse_nonfatal_unmatched_exit_warning(self) -> None:
        message = parse_line("WARN: EXIT IGNORED WITHOUT ENTRY IN=1 OUT=1\r\n")
        self.assertEqual(message.kind, "WARN")
        self.assertEqual(message.fields, ("EXIT", "IGNORED", "WITHOUT", "ENTRY", "IN=1", "OUT=1"))
        self.assertTrue(validate_device_message(message))


if __name__ == "__main__":
    unittest.main()
