from __future__ import annotations
import dataclasses
import enum
from typing import *
from rlsra import RegRestore, RegSpill, RegMove, ActiveInOut, Value

class Operator(enum.Enum):
    Add = enum.auto()
    Sub = enum.auto()
    Mul = enum.auto()
    Div = enum.auto()

    Eq = enum.auto()

class TreeKind(enum.Enum):
    LdLocal = enum.auto()
    StLocal = enum.auto()
    Const = enum.auto()
    Discard = enum.auto()
    BinOp = enum.auto()

    # Reserved for terminals
    Ret = enum.auto()
    Branch = enum.auto()
    Jmp = enum.auto()

@dataclasses.dataclass
class Tree:
    kind: TreeKind
    subtrees: list[Tree]
    operands: list
    parent: Tree | None
    block: BasicBlock

    # Assigned during Ir.reindex
    ir_idx: int = 0
    # Assigned during Rlsra.do_reverse_linear_scan or Lsra.do_linear_scan
    reg: int = -1
    pre_spills: list[RegSpill] = dataclasses.field(default_factory=list)
    pre_restores: list[RegRestore] = dataclasses.field(default_factory=list)
    pre_moves: list[RegMove] = dataclasses.field(default_factory=list)
    post_spills: list[RegSpill] = dataclasses.field(default_factory=list)
    post_restores: list[RegRestore] = dataclasses.field(default_factory=list)
    post_moves: list[RegMove] = dataclasses.field(default_factory=list)
    
    def tree_execution_order(self) -> Iterable[Tree]:
        for subtree in self.subtrees:
            yield from subtree.tree_execution_order()
        
        yield self
    
    def tree_reverse_execution_order(self) -> Iterable[Tree]:
        yield self

        for subtree in reversed(self.subtrees):
            yield from subtree.tree_reverse_execution_order()

    def dump(self, indent_level: int = 0):
        for tree in self.subtrees:
            tree.dump(indent_level + 4)

        indent = " " * indent_level
        
        for pre_spill in self.pre_spills:
            print(indent + str(pre_spill))

        for pre_restore in self.pre_restores:
            print(indent + str(pre_restore))

        for pre_move in self.pre_moves:
            print(indent + str(pre_move))

        reg = "" if self.parent == None else "(r" + str(self.reg) + ") "
        print(
            indent +
            "[" + str(self.ir_idx) + "] " +
            reg +
            self.kind.name +
            "(" + ", ".join(map(str, self.operands)) + ")"
        )
        
        for post_spill in self.post_spills:
            print(indent + str(post_spill))

        for post_restore in self.post_restores:
            print(indent + str(post_restore))

        for post_move in self.post_moves:
            print(indent + str(post_move))

@dataclasses.dataclass
class Statement:
    il_idx: int
    next_statement: Statement | None
    prev_statement: Statement | None
    tree: Tree

@dataclasses.dataclass
class BlockEdge:
    source: BasicBlock
    target: BasicBlock

    def __str__(self) -> str:
        return f"src {self.source} trgt {self.target}"

@dataclasses.dataclass
class BasicBlock:
    il_idx: int
    next_block: BasicBlock | None
    prev_block: BasicBlock | None
    first_statemenent: Statement | None
    last_statement: Statement | None

    # Assigned during Rlsra.do_linear_scan
    active_in_set: list[ActiveInOut] | None = None
    active_out_set: list[ActiveInOut] | None = None

    # Assigned during Ir.recompute_predecessors
    predecessors: list[BlockEdge] = dataclasses.field(default_factory=list)

    # Assigned during Ir.recompute_alive_sets
    alive_in_set: set[int] | None = None
    alive_out_set: set[int] | None = None

    def outgoing_edges(self) -> Iterable[BlockEdge]:
        # The assumption is that the operands of terminator nodes are block edges
        for operand in self.last_statement.tree.operands:
            assert isinstance(operand, BlockEdge), "operand isn't block edge"

            yield operand
    
    # Precondition : Ir.recompute_predecessors has been called
    def incoming_edges(self) -> Iterable[BlockEdge]:
        for edge in self.predecessors:
            yield edge

    def tree_execution_order(self) -> Iterable[Tree]:
        statement = self.first_statemenent
        while statement != None:
            yield from statement.tree.tree_execution_order()
            statement = statement.next_statement
    
    def tree_reverse_execution_order(self) -> Iterable[Tree]:
        statement = self.last_statement
        while statement != None:
            yield from statement.tree.tree_reverse_execution_order()
            statement = statement.prev_statement

    def append_tree(self, il_idx: int, tree: Tree) -> None:
        new_statement = Statement(il_idx=il_idx, tree=tree, next_statement=None, prev_statement=self.last_statement)
        if (self.last_statement == None):
            self.last_statement = new_statement
            self.first_statemenent = new_statement
            return
        self.last_statement.next_statement = new_statement
        self.last_statement = new_statement
    
    def __str__(self) -> str:
        return f"blk 0x{hex(self.il_idx)[2:].zfill(4)}"

class BasicBlockList:
    first: BasicBlock
    
    def __init__(self) -> None:
        self.first = BasicBlock(il_idx=0, next_block=None, prev_block=None, first_statemenent=None, last_statement=None)

    def get_or_insert_block_at(self, il_idx: int) -> BasicBlock:
        block = self.first
        while block.il_idx < il_idx:
            if block.next_block == None or block.next_block.il_idx > il_idx:
                break
            block = block.next_block
        
        if block.il_idx == il_idx:
            return block
        
        statement = block.first_statemenent
        while statement.il_idx < il_idx:
            if statement.next_statement == None:
                # il_idx lands past the block

                # insert a new block into the doubly linked list
                new_block = BasicBlock(il_idx=il_idx, next_block=block.next_block, prev_block=block.prev_block, first_statemenent=None, last_statement=None)
                block.next_block = new_block
                if (new_block.next_block != None):
                    new_block.next_block.prev_block = new_block

                return new_block
            statement = statement.next_statement
        
        if statement.il_idx > il_idx:
            # il_idx lands between two statements
            print("panic : il_idx lands between two statements")
            exit(1)
        
        # insert a new block into the doubly linked list
        new_block = BasicBlock(il_idx=il_idx, next_block=block.next_block, prev_block=block.prev_block, first_statemenent=None, last_statement=None)
        block.next_block = new_block
        if (new_block.next_block != None):
            new_block.next_block.prev_block = new_block
        
        new_block.first_statemenent = statement
        new_block.last_statement = block.last_statement

        jmp_statement = Statement(il_idx=0, next_statement=None, prev_statement=block.last_statement.prev_statement, tree=Tree(
            kind=TreeKind.Jmp,
            subtrees=[],
            operands=[BlockEdge(source=None, target=new_block)],
            parent=None,
            block=block
        ))
        if (jmp_statement.prev_statement != None):
            jmp_statement.prev_statement.next_statement = jmp_statement
            jmp_statement.il_idx = jmp_statement.prev_statement.il_idx
        else:
            jmp_statement.il_idx = block.il_idx
        if (block.last_statement is block.first_statemenent):
            block.first_statemenent = jmp_statement
        block.last_statement = jmp_statement
        new_block.first_statemenent.prev_statement = None

        return new_block

@dataclasses.dataclass
class Ir:
    blocks: BasicBlockList
    local_vars: int

    ir_idx_count: int = 0

    def no_successors(self) -> Iterable[BasicBlock]:
        for block in self.block_execution_order():
            # We assume blocks with no successors end with return trees
            if block.last_statement.tree.kind == TreeKind.Ret:
                yield block

    def recompute_predecessors(self) -> None:
        block = self.blocks.first
        while block != None:
            for edge in block.outgoing_edges():
                edge.source = block
                edge.target.predecessors.append(edge)
            block = block.next_block
    
    def recompute_alive_sets(self) -> None:
        # TODO : optimize this because it can be computed in a single O(n) pass if we start from "end" blocks (no successors / in infinite loops)
        while True:
            change_occured = False
            for block in self.block_execution_order():
                prev_alive = block.alive_in_set

                alive = set()
                for edge in block.outgoing_edges():
                    if edge.target.alive_in_set != None:
                        alive |= edge.target.alive_in_set

                for tree in block.tree_reverse_execution_order():
                    if tree.kind == TreeKind.LdLocal:
                        alive.add(tree.operands[0])
                    elif tree.kind == TreeKind.StLocal:
                        if tree.operands[0] in alive:
                            alive.remove(tree.operands[0])
            
                block.alive_in_set = alive

                if alive != prev_alive:
                    change_occured = True
            
            if not change_occured:
                break
        
        for block in self.block_execution_order():
            alive_out_set = set()
            for out_edge in block.outgoing_edges():
                alive_out_set |= out_edge.target.alive_in_set
            
            block.alive_out_set = alive_out_set

    def reindex(self) -> None:
        index = 0

        for tree in self.tree_execution_order():
            tree.ir_idx = index
            index += 1
        
        self.ir_idx_count = index
    
    def block_execution_order(self) -> Iterable[BasicBlock]:
        block = self.blocks.first
        while block != None:
            yield block
            block = block.next_block
    
    def tree_execution_order(self) -> Iterable[Tree]:
        block = self.blocks.first
        while block != None:
            statement = block.first_statemenent
            while statement != None:
                for t in statement.tree.tree_execution_order():
                    yield t
                statement = statement.next_statement
            block = block.next_block

    def dump(self) -> None:
        block = self.blocks.first
        while block != None:
            print(f"\nblk 0x{hex(block.il_idx)[2:].zfill(4)} - predecessors: [" + ", ".join(str(pred) for pred in block.predecessors) + "]")
            if block.alive_in_set == None:
                print("alive var in:", block.alive_in_set)
            if block.active_in_set != None:
                print("active var in:")
                for active_in in block.active_in_set:
                    print(active_in)

            statement = block.first_statemenent
            while statement != None:
                print(f"stmt 0x{hex(statement.il_idx)[2:].zfill(4)}")
                statement.tree.dump()

                statement = statement.next_statement

            if block.alive_out_set != None:
                print("alive var out:", block.alive_out_set)
            if block.active_out_set != None:
                print("active var out:")
                for active_out in block.active_out_set:
                    print(active_out)

            block = block.next_block