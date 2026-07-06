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
            default: y = 9'd0;
        endcase
    end
endmodule