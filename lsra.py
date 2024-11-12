from rlsra import *
from ir import *

class Lsra:
    # We reuse most data structures defined in rlsra because they can work both ways
    registers: list[Register]
    var_vals: list[Value]
    var_first_writes: dict[int, Tree | BasicBlock | None]
    tree_vals: list[Value]
    active_vals: list[Value]
    blocks_to_process: deque[BasicBlock]

    current_tree: Tree

    def __init__(self, num_regs: int) -> None:
        self.registers = [Register(active_val=None) for _ in range(num_regs)]
        self.var_vals = []
        self.var_first_writes = dict()
        self.tree_vals = []
        self.active_vals = []
        self.blocks_to_process = deque()
        self.current_tree = None
    
    def free_active_vals(self) -> None:
        new_active_vals = []
        for val in self.active_vals:
            if isinstance(val.last_use, BasicBlock):
                new_active_vals.append(val)
                continue
            
            if val.last_use is None or val.last_use.ir_idx <= self.current_tree.ir_idx:
                self.registers[val.active_in].active_val = None
                val.active_in = None
            else:
                new_active_vals.append(val)
        
        self.active_vals = new_active_vals
    
    def free_tree_vals(self) -> None:
        new_tree_vals = []
        for val in self.tree_vals:
            if val.of.ir_idx >= self.current_tree.ir_idx:
                new_tree_vals.append(val)
        
        self.tree_vals = new_tree_vals
    
    # Make a value active (active_in will have the index of a register)
    def activate(self, val: Value, restore: bool = True, forbid_restores: list[int] = []) -> None:
        assert val.last_use is not None
        assert val.active_in is None

        # TODO : register preference sets. Variables should prefer being stored in a register they were previously in in priority.
        # If they can't have it, or if the value is a tree temp, it should prioritize reusing an operand register.

        for reg_i, reg in enumerate(self.registers):
            if restore and reg_i in forbid_restores:
                continue

            if reg.active_val == None:
                val.active_in = reg_i
                reg.active_val = val
                self.active_vals.append(val)

                if restore:
                    self.current_tree.pre_restores.append(RegRestore(val, reg_i))

                return
        
        # No free registers, spill a value
        # Best candidate heuristic : furthest last use
        best_val: Value | None = None
        for active_val in self.active_vals:
            assert active_val.last_use != None # Would've been freed

            if restore and active_val.active_in in forbid_restores:
                continue

            """
            # Check if the val is a candidate (it's not about to be used)
            if isinstance(active_val.of, int):
                if any(subtree.kind == TreeKind.LdLocal and subtree.operands[0] == active_val.of for subtree in self.current_tree.subtrees):
                    # Local variable is an operand that needs to be in a register
                    continue
            else:
                if active_val.last_use is self.current_tree:
                    # Tree temp is an operand
                    continue
            """
            
            if best_val is None:
                best_val = active_val
            elif isinstance(active_val.last_use, BasicBlock):
                continue
            else:
                if isinstance(best_val.last_use, BasicBlock):
                    best_val = active_val
                else:
                    if active_val.last_use.ir_idx > best_val.last_use.ir_idx:
                        best_val = active_val

        assert best_val is not None, "no spill candidates"

        assert best_val.active_in is not None

        self.current_tree.pre_spills.append(RegSpill(val=best_val, reg=best_val.active_in))

        val.active_in = best_val.active_in
        self.registers[val.active_in].active_val = val
        self.active_vals.append(val)

        if restore:
            self.current_tree.pre_restores.append(RegRestore(val=val, reg=val.active_in))

        best_val.active_in = None
        self.active_vals.remove(best_val)
    
    def reset_var_vals_and_regs(self) -> None:
        assert self.tree_vals == []

        for val in self.var_vals:
            if val.active_in != None:
                self.registers[val.active_in].active_val = None
                val.active_in = None
                self.active_vals.remove(val)
            
            val.last_use = None
        
        self.var_first_writes = dict()
        
        assert self.active_vals == []
        assert not any(reg.active_val != None for reg in self.registers), str(self.registers)
    
    def get_tree_val(self, tree: Tree) -> Value:
        if tree.kind == TreeKind.LdLocal:
            return self.var_vals[tree.operands[0]]

        for tree_val in self.tree_vals:
            if tree_val.of is tree:
                return tree_val
        
        assert False, "unreachable"
    
    # Do LSRA
    # Preconditions : recompute_predecessors, recompute_alive_in_sets, reindex all executed
    def do_linear_scan(self, ir: Ir) -> None:
        # Set up all the values corresponding to local variables
        for i in range(ir.local_vars):
            self.var_vals.append(Value(of=i, active_in=None, last_use=None))
        
        # Queue up the first block to be processed
        self.blocks_to_process.append(ir.blocks.first)

        while len(self.blocks_to_process) != 0:
            block = self.blocks_to_process.popleft()
            
            self.reset_var_vals_and_regs()

            # Select predecessor
            selected_predecessor = None
            for predecessor in block.predecessors:
                if predecessor.source.active_out_set is not None:
                    selected_predecessor = predecessor
                    break
            
            # Setup first writes for local variables
            for in_edge in block.predecessors:
                for alive in in_edge.source.alive_in_set:
                    self.var_first_writes[alive] = in_edge.source

            # Setup last uses for local variables
            for out_edge in block.outgoing_edges():
                for alive in out_edge.target.alive_in_set:
                    self.var_vals[alive].last_use = out_edge.target
            
            for tree in block.tree_reverse_execution_order():
                if tree.kind == TreeKind.LdLocal:
                    val = self.var_vals[tree.operands[0]]
                    if val.last_use == None:
                        val.last_use = tree.parent
            
            # Activate values that should be active from the predecessors
            if selected_predecessor is not None:
                block.active_in_set = selected_predecessor.source.active_out_set
                for active_in in block.active_in_set:
                    val = active_in.val
                    reg = active_in.reg
                    val.active_in = reg
                    self.registers[reg].active_val = val
                    self.active_vals.append(val)
            else:
                block.active_in_set = []

            # Linear scan
            for tree in block.tree_execution_order():
                self.current_tree = tree

                if tree.ir_idx == 17:
                    print([str(val) for val in self.active_vals])

                # Make sure all the operands are in a register
                for subtree in tree.subtrees:
                    tree_val = self.get_tree_val(subtree)
                    if tree_val.active_in is None:
                        self.activate(tree_val)

                self.free_active_vals()

                if tree.kind == TreeKind.StLocal:
                    # Special case : storing locals
                    src_val = self.get_tree_val(tree.subtrees[0])                    
                    dst_val = self.var_vals[tree.operands[0]]

                    if dst_val.of not in self.var_first_writes:
                        self.var_first_writes[dst_val.of] = tree

                    src_reg = tree.subtrees[0].reg
                    if dst_val.active_in is None:
                        self.activate(dst_val, restore=self.var_first_writes[dst_val.of] is not tree, forbid_restores=[src_reg])
                    dst_reg = dst_val.active_in
                    tree.operands.append(dst_reg)

                    if src_reg != dst_reg:
                        tree.post_moves.append(RegMove(val_from=src_val, reg_from=src_reg, val_to=dst_val, reg_to=dst_reg))                        
                elif tree.kind == TreeKind.LdLocal:
                    # Special case : loading locals
                    var_val = self.var_vals[tree.operands[0]]

                    if var_val.active_in == None:
                        self.activate(var_val)
                    tree.reg = var_val.active_in
                else:
                    if tree.parent != None:
                        tree_val = Value(of=tree, active_in=None, last_use=tree.parent)
                        self.activate(tree_val, restore=False)
                        tree.reg = tree_val.active_in

                        self.tree_vals.append(tree_val)
                
                # Free up tree vals we won't use anymore
                self.free_tree_vals()
            
            # Create active out set
            active_out_set = []
            for active_val in self.active_vals:
                assert isinstance(active_val.of, int)
                active_out_set.append(ActiveInOut(val=active_val, reg=active_val.active_in))
            block.active_out_set = active_out_set

            # Queue up successors
            for out_edge in block.outgoing_edges():
                if out_edge.target.active_in_set is None:
                    self.blocks_to_process.append(out_edge.target)
