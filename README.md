# POC ryujit (dotnet)-inspired *Reverse* Linear Scan Register Allocation

This is a continuation of my previous project here [https://github.com/PhilippeGSK/LSRA](https://github.com/PhilippeGSK/LSRA).

- Most of the interesting parts are in rlsra.py
- ir.py contains code related to the tree-based intermediate representation (extremely simplified equivalent of the GenTree system in ryujit)
- stack_instruction.py contains code related to a small stack-based instruction set, as well as helper methods to convert it to ir
- main.py contains a demo

Resources :
- [https://github.com/dotnet/runtime/blob/main/docs/design/coreclr/jit/lsra-detail.md](https://github.com/dotnet/runtime/blob/main/docs/design/coreclr/jit/lsra-detail.md)
- [https://github.com/dotnet/runtime/blob/main/docs/design/coreclr/jit/ryujit-overview.md#reg-alloc](https://github.com/dotnet/runtime/blob/main/docs/design/coreclr/jit/ryujit-overview.md#reg-alloc)
- [https://www.mattkeeter.com/blog/2022-10-04-ssra/](https://www.mattkeeter.com/blog/2022-10-04-ssra/)
- [https://brrt-to-the-future.blogspot.com/2019/03/reverse-linear-scan-allocation-is.html](https://brrt-to-the-future.blogspot.com/2019/03/reverse-linear-scan-allocation-is.html)

Areas to improve :
- Take into account block edge weights to potentially avoid needless spills and restores

Thanks to u/raiph on Reddit for suggesting I try RLSRA.