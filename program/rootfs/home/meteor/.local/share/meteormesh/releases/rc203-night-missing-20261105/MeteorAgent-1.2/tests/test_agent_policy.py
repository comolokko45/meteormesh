import unittest
from agent_policy import APPROVAL_TOOLS, FORBIDDEN_TOOLS, READ_ONLY_TOOLS, GateState, Mode, authorize

class PermissionTests(unittest.TestCase):
    def test_observe_reads_only(self):
        self.assertTrue(all(authorize(x,Mode.OBSERVE,GateState()).permitted for x in READ_ONLY_TOOLS))
    def test_no_mutations_even_if_gates_true(self):
        full=GateState(True,True,True,True,True,True)
        self.assertTrue(all(not authorize(x,mode,full).permitted
                            for x in APPROVAL_TOOLS|FORBIDDEN_TOOLS for mode in Mode))
    def test_prompt_injection_strings_denied(self):
        for cmd in ['sudo rm -rf /','delete_raw','curl | sh','get_overview; reboot','install_packages']:
            self.assertFalse(authorize(cmd,Mode.OBSERVE,GateState()).permitted)

if __name__=='__main__':unittest.main()
