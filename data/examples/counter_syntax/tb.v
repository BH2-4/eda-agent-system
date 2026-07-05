// Testbench for counter (correct reference design)
`timescale 1ns/1ps
module tb;
    reg        clk;
    reg        rst;
    wire [7:0] count;

    integer i;

    // DUT
    counter uut (
        .clk(clk),
        .rst(rst),
        .count(count)
    );

    // Clock: 10ns period
    initial clk = 0;
    always #5 clk = ~clk;

    initial begin
        // Reset for at least 2 cycles
        rst = 1;
        #10 rst = 0;

        // Run several cycles so counter can increment
        repeat(10) @(posedge clk);
        #1; // settle NBA before sampling (count 是 NBA 赋值,posedge 时未生效)

        // After 10 increments starting from 0, count should be 10
        if (count !== 8'd10) begin
            $display("TEST_FAIL count_value");
            $finish;
        end

        $display("TEST_PASS 1/1");
        $finish;
    end

endmodule
