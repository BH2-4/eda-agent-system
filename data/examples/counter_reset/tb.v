`timescale 1ns/1ps
module tb;
    reg clk;
    reg rst;
    wire [7:0] count;

    counter_reset dut (
        .clk(clk),
        .rst(rst),
        .count(count)
    );

    // clock
    initial clk = 0;
    always #5 clk = ~clk;

    integer i;

    initial begin
        // reset for several cycles
        rst = 1;
        repeat (3) @(posedge clk);
        // after reset deasserts, on the next edge count should have been 0 during reset
        // sample count while still in reset (synchronous reset takes effect on edge)
        // At this point rst=1 was sampled on the last 3 posedges -> count should be 0
        #1; // small offset to avoid NBA read race
        if (count !== 8'd0) begin
            $display("TEST_FAIL reset_value");
            $finish;
        end

        // release reset and check increment
        rst = 0;
        @(posedge clk); #1;
        if (count !== 8'd1) begin
            $display("TEST_FAIL count_value");
            $finish;
        end
        @(posedge clk); #1;
        if (count !== 8'd2) begin
            $display("TEST_FAIL count_value");
            $finish;
        end

        $display("TEST_PASS 1/1");
        $finish;
    end
endmodule
