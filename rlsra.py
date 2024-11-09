from __future__ import annotations
import dataclasses
from ir import *
import ir as irepr
from collections import deque

@dataclasses.dataclass
class Register:
    # Value active in the registers
    active_val: Value | None

@dataclasses.dataclass
class Value:
    # Local variable or tree temp
    of: int | Tree
    # Register, if value is active
    active_in: int | None
    last_use: Tree | None

    def __eq__(self, value: object) -> bool:
        return self is value

    def __str__(self) -> str:
        if isinstance(self.of, int):
            return f"local {self.of}"
        else:
            return f"tree {self.of.ir_idx}"

@dataclasses.dataclass
class RegRestore:
    val: Value
    reg: int

    def __str__(self) -> str:
        return f"restore {self.val} into r{self.reg}"

@dataclasses.dataclass
class RegSpill:
    val: Value
    reg: int

    def __str__(self) -> str:
        return f"spill {self.val} from r{self.reg}"
    
@dataclasses.dataclass
class RegMove:
    val_from: Value
    reg_from: int
    val_to: Value
    reg_to: int

    def __str__(self) -> str:
        return f"spill {self.val} from r{self.reg}"

# The main class that performs RLSRA
class Rlsra:
    registers: list[Register]
    var_vals: list[Value]
    tree_vals: list[Value]
    active_vals: list[Value]
    blocks_to_process: deque[BasicBlock]

    current_tree: Tree

    def __init__(self, num_regs: int = 4) -> None:
        self.registers = [Register(active_val=None) for _ in range(num_regs)]
        self.var_vals = []
        self.tree_vals = []
        self.active_vals = []
        self.blocks_to_process = deque()
        self.current_tree = None

    # Spills a value (actually inserts a restore, because we're processing the code in reverse order)
    def spill(self, val: Value) -> None:
        self.current_tree.post_restores.append(RegRestore(val=val, reg=val.active_in))
        self.registers[val.active_in].active_val = None
        val.active_in = None
        self.active_vals.remove(val)

    # Activates a value by giving it a register. Can spill other values
    def activate(self, val: Value) -> None:
        # Try to find a free register activate the value with
        for reg_i, reg in enumerate(self.registers):
            if reg.active_val == None:
                val.active_in = reg_i
                reg.active_val = val

                self.active_vals.append(val)

                return
        
        # We couldn't find a free register, need to spill a value
        # The heuristic used to find the best value to spill is to pick the one that was used last in the code
        best_val = None
        for active_val in self.active_vals:
            if active_val.last_use.ir_idx <= val.last_use.ir_idx:
                # The value will be used before or at the same time as the current value, we cannot spill it
                continue

            if best_val == None:
                best_val = active_val
            elif active_val.last_use.ir_idx > best_val.last_use.ir_idx:
                best_val = active_val
        
        assert best_val != None, "no spill candidate"

        reg_i: int = best_val.active_in
        reg = self.registers[reg_i]

        self.spill(best_val)
        val.active_in = reg_i
        reg.active_val = val
        self.active_vals.append(val)

    def get_current_tree_val(self) -> Value | None:
        for tree_val in self.tree_vals:
            if tree_val.of is self.current_tree:
                return tree_val
    
    # Do RLSRA
    # Preconditions : recompute_predecessors, reindex all executed
    def do_reverse_linear_scan(self, ir: Ir) -> None:
        # Set up all the values corresponding to local variables
        for i in range(ir.local_vars):
            self.var_vals.append(Value(of=i, active_in=None, last_use=None))

        # We start from the end : we queue up the blocks with no successors to be processed
        for no_successors in ir.no_successors():
            self.blocks_to_process.append(no_successors)
        
        while len(self.blocks_to_process) != 0:
            block = self.blocks_to_process.popleft()
            
            for tree in block.tree_reverse_execution_order():
                self.current_tree = tree

                # Special case : loading a local variable
                # TODO : PROCESS THIS WITH THE SUBTREES TO NOT END UP WITH NEEDLESS SPILLS AND RESTORES
                if tree.kind == irepr.TreeKind.LdLocal:
                    tree_val = self.get_current_tree_val()
                    assert tree_val != None # We cannot have LdLocal without using its result

                    val = self.var_vals[tree.operands[0]]

                    if val.active_in == None:
                        # If the local variable val is not already active, we activate it using the tree val (where it's expected to go)
                        if tree_val.active_in == None:
                            self.activate(tree_val)

                        val.active_in = tree_val.active_in
                        tree.reg = val.active_in
                        # Generate a spill if we need to because this variable is expected to be found in memory
                        if (val.last_use != None):
                            tree.post_spills.append(RegSpill(val=val, reg=val.active_in))
                        self.registers[val.active_in].active_val = val
                        self.active_vals.append(val)

                        tree_val.active_in = None
                        self.active_vals.remove(tree_val)
                        self.tree_vals.remove(tree_val)
                    else:
                        # The local variable val is already active
                        if tree_val.active_in == None:
                            # If the tree val isn't, we just generate a spill for the local variable into the tree val
                            tree.post_spills.append(RegSpill(val=tree_val, reg=val.active_in))
                            tree.reg = val.active_in
                        else:
                            # If the tree val is, we just generate a move for the local variable into the tree val register
                            tree.reg = val.active_in
                            tree.post_moves.append(RegMove(
                                val_from=val,
                                reg_from=val.active_in,
                                val_to=tree_val,
                                reg_to=tree_val.active_in
                            ))
                    
                    val.last_use = self.current_tree

                # Special case : storing into a local variable. This also removes the local variable from the active variables
                elif tree.kind == irepr.TreeKind.StLocal:
                    val = self.var_vals[tree.operands[0]]
                    subtree = tree.subtrees[0]

                    if val.active_in != None:
                        # If it's already active, we write the output to the regsiter it's active in
                        subtree_val = Value(of=subtree, active_in=val.active_in, last_use=tree)
                        self.registers[subtree_val.active_in].active_val = subtree_val
                        tree.operands.append(val.active_in)
                        self.active_vals.append(subtree_val)
                        self.tree_vals.append(subtree_val)

                        val.active_in = None
                        self.active_vals.remove(val)
                    else:
                        # If not, we first find a register to write it into (reusing the register of the subtree), then add a spill
                        subtree_val = Value(of=subtree, active_in=None, last_use=tree)
                        self.activate(subtree_val)
                        self.tree_vals.append(subtree_val)

                        tree.operands.append(subtree_val.active_in)
                        tree.post_spills.append(RegSpill(val=subtree_val, reg=subtree_val.active_in))
                else:
                    tree_val = self.get_current_tree_val()
                    if tree_val != None:
                        # If the tree corresponds to a used tree value (later in execution order), we write the output where it's expected to be 
                        if tree_val.active_in != None:
                            # If it's already active, we write the output to the register it's active in
                            tree.reg = tree_val.active_in
                            self.registers[tree_val.active_in].active_val = None
                            tree_val.active_in = None
                        else:
                            # If not, we first find a register to do that, then add a spill
                            self.activate(tree_val)
                            tree.reg = tree_val.active_in
                            tree.post_spills.append(RegSpill(val=tree_val, reg=tree_val.active_in))
                            self.registers[tree_val.active_in].active_val = None
                            tree_val.active_in = None

                        self.active_vals.remove(tree_val)
                        self.tree_vals.remove(tree_val)

                    # Generate a use for all the subtrees and activate them because by this point we must have all operands in registers
                    for subtree in tree.subtrees:
                        subtree_val = Value(of=subtree, active_in=None, last_use=tree)
                        self.activate(subtree_val)
                        self.tree_vals.append(subtree_val)

        