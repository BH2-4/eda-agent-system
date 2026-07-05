// counter_reset bug: reset value wrong (0xFF instead of 0x00)
module counter_reset (
    input  wire clk,
    input  wire rst,
    output reg  [7:0] count
);
    always @(posedge clk) begin
        if (rst)
            count <= 8'hFF;   // BUG: should be 8'd0
        else
            count <= count + 1'b1;
    end
endmodule
