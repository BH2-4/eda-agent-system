`timescale 1ns/1ps
module tb;
    reg clk;
    reg rst;
    reg [7:0] a;
    reg [7:0] b;
    wire [8:0] sum;

    adder_pipe dut (
        .clk(clk),
        .rst(rst),
        .a(a),
        .b(b),
        .sum(sum)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    initial begin
        rst = 1;
        a = 8'd0;
        b = 8'd0;
        // hold reset for 2 clocks
        @(posedge clk);
        @(posedge clk);
        #1 rst = 0;

        // drive a=200, b=200 -> expected sum=400 (9'd400) for correct [8:0] hardware
        @(negedge clk);
        a = 8'd200;
        b = 8'd200;

        // wait one pipeline stage, settle past NBA
        repeat(3) @(posedge clk);
        #1;

        // Correct hardware: sum should hold 400 -> requires [8:0] register.
        // Buggy [7:0] truncates -> observed 144. Compare against 9-bit expected.
        if (sum !== 9'd400) begin
            $display("TEST_FAIL sum_overflow");
            $finish;
        end

        $display("TEST_PASS 1/1");
        $finish;
    end
endmodule
