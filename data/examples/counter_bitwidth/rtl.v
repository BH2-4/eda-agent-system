module counter(
    input clk,
    input rst,
    output reg [3:0] count
);
    // BUG: bitwidth too small. Should be [7:0]; wraps at 15.
    always @(posedge clk) begin
        if (rst)
            count <= 4'd0;
        else
            count <= count + 4'd1;
    end
endmodule
