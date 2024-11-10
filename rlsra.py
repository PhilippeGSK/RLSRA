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
    # If the variable was last used in another block we'll have last_use be that block
    last_use: Tree | BasicBlock | None

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

# Inserted into the active in / active out sets of blocks
@dataclasses.dataclass
class ActiveInOut:
    val: Value
    reg: int

    def __str__(self) -> str:
        return f"{self.val} in r{self.reg}"

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
            if active_val.last_use is val.last_use:
                # The value will be used before or at the same time as the current value, we cannot spill it
                continue

            if best_val == None:
                best_val = active_val
            elif isinstance(active_val.last_use, irepr.BasicBlock):
                best_val = active_val
                break
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
    
    def reset_var_vals_and_regs(self) -> None:
        assert self.tree_vals == []

        for val in self.var_vals:
            if val.active_in != None:
                self.registers[val.active_in].active_val = None
                val.active_in = None
                self.active_vals.remove(val)
            
            val.last_use = None
        
        assert self.active_vals == []
        assert not any(reg.active_val != None for reg in self.registers)
    
    # Setup a local variable and use its register as the output of the LdLocal subtree
    def use_local(self, subtree: Tree):
        assert subtree.kind == irepr.TreeKind.LdLocal

        val = self.var_vals[subtree.operands[0]]

        val_was_used = val.last_use != None
        val_was_active = val.active_in != None
        val.last_use = self.current_tree

        # Activate the variable if it wasn't already
        if not val_was_active:
            self.activate(val)

        subtree.reg = val.active_in

        # If the variable is expected to be found in memory, generate a spill
        if val_was_used and not val_was_active:
            subtree.post_spills.append(RegSpill(val=val, reg=val.active_in))
    
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

            # Since we're processing blocks in reverse order we select active out sets
            # TODO : Add a heuristic for selection (edge weight?)
            selected_out_edge = None
            for out_edge in block.outgoing_edges():
                if out_edge.target.active_in_set != None:
                    selected_out_edge = out_edge
                    break
            
            # Select active out set
            if selected_out_edge == None:
                # For blocks that have no successors
                assert block.last_statement.tree.kind == irepr.TreeKind.Ret
                block.active_out_set = []
            else:
                block.active_out_set = selected_out_edge.target.active_in_set

            self.reset_var_vals_and_regs()

            # Mark the values that can be potentially used later as alive
            for out_edge in block.outgoing_edges():
                if out_edge.target.alive_in_set == None:
                    # For all we know, this block might use all local variables
                    for var_val in self.var_vals:
                        var_val.last_use = out_edge.target
                    break
                else:
                    for alive in out_edge.target.alive_in_set:
                        alive.last_use = out_edge.target

            # Activate the values in the active out set
            if selected_out_edge != None:
                for active_out in block.active_out_set:
                    assert isinstance(active_out.val.of, int)
                    
                    active_out.val.active_in = active_out.reg
                    self.registers[active_out.reg].active_val = active_out.val
                    self.active_vals.append(active_out.val)
            
            for tree in block.tree_reverse_execution_order():
                self.current_tree = tree

                # This is processed with the subtrees (simplifies the algorithm to avoid needless loads and spills)
                if tree.kind == irepr.TreeKind.LdLocal:
                    pass
                # Special case : storing into a local variable. This also removes the local variable from the active variables if it was active
                elif tree.kind == irepr.TreeKind.StLocal:
                    val = self.var_vals[tree.operands[0]]
                    val_was_used = val.last_use != None
                    subtree = tree.subtrees[0]

                    if subtree.kind == irepr.TreeKind.LdLocal:
                        # Special case : transfering a local variable to another
                        val_from = self.var_vals[subtree.operands[0]]
                        self.use_local(subtree)

                        if val.active_in != None:
                            # If it's already active, we emit a move
                            subtree.post_moves.append(RegMove(val_from=val_from, reg_from=val_from.active_in, val_to=val, reg_to=val.active_in))
                            tree.operands.append(val.active_in)

                            self.registers[val.active_in].active_val = None
                            val.active_in = None
                            val.last_use = None
                            self.active_vals.remove(val)
                        else:
                            # If it's not already active but will be used later, we emit a spill
                            if val_was_used:
                                subtree.post_spills.append(RegSpill(val=val, reg=val_from.active_in))
                            tree.operands.append(val)
                    else:
                        if val.active_in != None:
                            # If it's already active, we write the output to the regsiter it's active in
                            subtree_val = Value(of=subtree, active_in=val.active_in, last_use=tree)
                            self.registers[subtree_val.active_in].active_val = subtree_val
                            tree.operands.append(val.active_in)
                            self.active_vals.append(subtree_val)
                            self.tree_vals.append(subtree_val)

                            val.active_in = None
                            val.last_use = None
                            self.active_vals.remove(val)
                        else:
                            # If not, we first find a register to write it into (reusing the register of the subtree), then add a spill
                            subtree_val = Value(of=subtree, active_in=None, last_use=tree)
                            val.last_use = None
                            self.activate(subtree_val)
                            self.tree_vals.append(subtree_val)

                            tree.operands.append(subtree_val.active_in)
                            tree.post_spills.append(RegSpill(val=val, reg=subtree_val.active_in))
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
                        # Special case : loading a local variable
                        if subtree.kind == irepr.TreeKind.LdLocal:
                            self.use_local(subtree)
                        else:
                            subtree_val = Value(of=subtree, active_in=None, last_use=tree)
                            self.activate(subtree_val)
                            self.tree_vals.append(subtree_val)
            
            # Create an active in set
            active_in_set = []
            for val in self.active_vals:
                active_in_set.append(ActiveInOut(val=val, reg=val.active_in))
            
            block.active_in_set = active_in_set

            # Create an alive in set
            alive_in_set = []
            for val in self.var_vals:
                if val.last_use != None:
                    alive_in_set.append(val)
            
            block.alive_in_set = alive_in_set

            # Queue up blocks that haven't been processed
            for predecessor in block.predecessors:
                if predecessor.source.active_in_set == None:
                    self.blocks_to_process.append(predecessor.source)

        