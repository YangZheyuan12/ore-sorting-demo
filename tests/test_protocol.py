import unittest

from sorting_demo.classifier_adapter import demo_batch
from sorting_demo.protocol import batch_commands, parse_line, validate_device_message


class ProtocolTests(unittest.TestCase):
    def test_batch_commands_are_deterministic(self) -> None:
        commands = batch_commands(demo_batch())
        self.assertEqual(commands[0], "RESET")
        self.assertEqual(commands[1], "BATCH 5")
        self.assertEqual(commands[2], "PUSH S01 ORE")
        self.assertEqual(commands[3], "PUSH S02 WASTE")

    def test_parse_device_event(self) -> None:
        message = parse_line("EVENT EXIT S02 WASTE\r\n")
        self.assertEqual(message.kind, "EVENT")
        self.assertEqual(message.fields, ("EXIT", "S02", "WASTE"))
        self.assertTrue(validate_device_message(message))


if __name__ == "__main__":
    unittest.main()
