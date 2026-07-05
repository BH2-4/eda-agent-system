// adder_pipe_comb: combinational adder/subtractor with INJECTED BUG
// Bug: case statement missing default branch -> inferred latch for sel=2,3
module adder_pipe_comb(
    input  [7:0] a,
    input  [7:0] b,
    input  [1:0] sel,
    output reg [8:0] y
);
    always @(*) begin
        case (sel)
            2'd0: y = a + b;
            2'd1: y = a - b;
            // BUG: no default branch -> latch when sel=2 or sel=3
        endcase
    end
endmodule
