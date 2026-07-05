// Bug-injected: sum declared [7:0] instead of [8:0] -> overflow on 200+200
module adder_pipe(
    input clk,
    input rst,
    input [7:0] a,
    input [7:0] b,
    output reg [7:0] sum
);
    always @(posedge clk) begin
        if (rst)
            sum <= 8'd0;
        else
            sum <= a + b;
    end
endmodule
