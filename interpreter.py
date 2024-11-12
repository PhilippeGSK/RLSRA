from ir import *

class Interpreter:
    ir: Ir
    registers: list[int]
    spilled_local_vals: dict[int, int]
    spilled_tree_vals: dict[int, int]
    current_block: BasicBlock
    spill_count: int
    restore_count: int
    move_count: int

    def __init__(self, num_regs: int, ir: Ir) -> None:
        self.ir = ir
        self.registers = [None for _ in range(num_regs)]
        self.spilled_local_vals = dict()
        self.spilled_tree_vals = dict()
        self.current_block = ir.blocks.first

        self.spill_count = 0
        self.restore_count = 0
        self.move_count = 0
    
    def jump(self, edge: BlockEdge) -> None:
        self.current_block = edge.target

        active_out_set = edge.source.active_out_set
        active_in_set = edge.target.active_in_set

        new_registers = self.registers[:]

        for active_out in active_out_set:
            if not any (active_out.val.of == active_in.val.of for active_in in active_in_set):
                self.spilled_local_vals[active_out.val.of] = self.registers[active_out.reg]
                self.spill_count += 1
        
        for active_in in active_in_set:
            if not any(active_out.val.of == active_in.val.of for active_out in active_out_set):
                new_registers[active_in.reg] = self.spilled_local_vals[active_in.val.of]
                self.restore_count += 1
        
        for active_out in active_out_set:
            for active_in in active_in_set:
                if active_out.val.of == active_in.val.of:
                    new_registers[active_in.reg] = self.registers[active_out.reg]
                    self.move_count += 1
            
        self.registers = new_registers

    def run(self) -> int:
        while True:
            for tree in self.current_block.tree_execution_order():
                # Pre spills, restores, moves
                new_registers = self.registers[:]

                for spill in tree.pre_spills:
                    if isinstance(spill.val.of, int):
                        self.spilled_local_vals[spill.val.of] = self.registers[spill.reg]
                    else:
                        self.spilled_tree_vals[spill.val.of.ir_idx] = self.registers[spill.reg]
                    self.spill_count += 1
                
                for restore in tree.pre_restores:
                    if isinstance(restore.val.of, int):
                        new_registers[restore.reg] = self.spilled_local_vals[restore.val.of]
                    else:
                        new_registers[restore.reg] = self.spilled_tree_vals[restore.val.of.ir_idx]
                    self.restore_count += 1
                
                for move in tree.pre_moves:
                    new_registers[move.reg_to] = self.registers[move.reg_from]
                    self.move_count += 1
                
                self.registers = new_registers

                match tree.kind:
                    case TreeKind.LdLocal:
                        # Handled by the reg allocator
                        pass
                    case TreeKind.StLocal:
                        # Handled by the reg allocator
                        pass
                    case TreeKind.Const:
                        self.registers[tree.reg] = tree.operands[0]
                    case TreeKind.Discard:
                        # Do nothing
                        pass
                    case TreeKind.BinOp:
                        lhs = self.registers[tree.subtrees[0].reg]
                        rhs = self.registers[tree.subtrees[1].reg]

                        match tree.operands[0]:
                            case Operator.Add:
                                res = lhs + rhs
                            case Operator.Sub:
                                res = lhs - rhs
                            case Operator.Mul:
                                res = lhs * rhs
                            case Operator.Div:
                                res = lhs // rhs
                            case Operator.Eq:
                                res = 1 if lhs == rhs else 0
                        
                        self.registers[tree.reg] = res
                    case TreeKind.Ret:
                        return self.registers[tree.subtrees[0].reg]
                    case TreeKind.Branch:
                        if self.registers[tree.subtrees[0].reg] == 1:
                            self.jump(tree.operands[0])
                        else:
                            self.jump(tree.operands[1])
                    case TreeKind.Jmp:
                        self.jump(tree.operands[0])

                # Post spills, restores, moves
                new_registers = self.registers[:]

                for spill in tree.post_spills:
                    if isinstance(spill.val.of, int):
                        self.spilled_local_vals[spill.val.of] = self.registers[spill.reg]
                    else:
                        self.spilled_tree_vals[spill.val.of.ir_idx] = self.registers[spill.reg]
                    self.spill_count += 1
                
                for restore in tree.post_restores:
                    if isinstance(restore.val.of, int):
                        new_registers[restore.reg] = self.spilled_local_vals[restore.val.of]
                    else:
                        new_registers[restore.reg] = self.spilled_tree_vals[restore.val.of.ir_idx]
                    self.restore_count += 1
                
                for move in tree.post_moves:
                    new_registers[move.reg_to] = self.registers[move.reg_from]
                    self.move_count += 1
                
                self.registers = new_registers