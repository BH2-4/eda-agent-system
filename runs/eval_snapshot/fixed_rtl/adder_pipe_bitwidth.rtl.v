module adder_pipe(
    input clk,
    input rst,
    input [7:0] a,
    input [7:0] b,
    output reg [8:0] sum
);
    always @(posedge clk) begin
        if (rst)
            sum <= 9'd0;
        else
            sum <= a + b;
    end
endmodule