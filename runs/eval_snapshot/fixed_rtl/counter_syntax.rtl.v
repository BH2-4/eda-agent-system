// counter_syntax: fixed RTL
// Top module: counter
module counter (
    input  wire        clk,
    input  wire        rst,
    output reg [7:0]   count
);

    always @(posedge clk) begin
        if (rst)
            count <= 8'd0;
        else
            count <= count + 8'd1;
    end

endmodule