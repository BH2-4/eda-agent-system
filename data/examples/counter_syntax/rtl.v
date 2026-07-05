// counter_syntax: buggy RTL (syntax error - "alwayss" misspelled)
// Top module: counter
module counter (
    input  wire        clk,
    input  wire        rst,
    output reg [7:0]   count
);

    // BUG: "alwayss" is misspelled (extra 's') -> syntax error
    alwayss @(posedge clk) begin
        if (rst)
            count <= 8'd0;
        else
            count <= count + 8'd1;
    end

endmodule
