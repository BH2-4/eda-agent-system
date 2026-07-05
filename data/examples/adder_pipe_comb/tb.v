// Testbench for adder_pipe_comb: detects missing-default latch bug
`timescale 1ns/1ps
module tb;
    reg  [7:0] a;
    reg  [7:0] b;
    reg  [1:0] sel;
    wire [8:0] y;

    adder_pipe_comb dut (
        .a(a),
        .b(b),
        .sel(sel),
        .y(y)
    );

    integer fail;
    initial begin
        fail = 0;

        // Step 1: sel=0 -> y = a + b (write a known nonzero value into y)
        a = 8'd5;
        b = 8'd3;
        sel = 2'd0;
        #1;
        if (y !== 9'd8) begin
            $display("TEST_FAIL comb_latch");
            fail = 1;
            $finish;
        end

        // Step 2: sel=2 -> should hit default and y=0, but bug keeps old y=8
        sel = 2'd2;
        #1;
        if (y !== 9'd0) begin
            $display("TEST_FAIL comb_latch");
            fail = 1;
            $finish;
        end

        // Step 3: sel=3 -> also default, expect y=0
        sel = 2'd3;
        #1;
        if (y !== 9'd0) begin
            $display("TEST_FAIL comb_latch");
            fail = 1;
            $finish;
        end

        // All checks passed
        $display("TEST_PASS 1/1");
        $finish;
    end
endmodule
