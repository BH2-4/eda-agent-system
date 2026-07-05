`timescale 1ns/1ps
module tb;
    reg clk;
    reg rst;
    reg in;
    wire out;

    integer i;

    // DUT
    tiny_fsm_comb dut (
        .clk(clk),
        .rst(rst),
        .in(in),
        .out(out)
    );

    // clock
    always #5 clk = ~clk;
    initial clk = 0;

    // reset + stimulus
    initial begin
        rst = 1;
        in  = 0;
        #10 rst = 0;        // release reset after 2 edges
        // drive in=1, should trigger S0 -> S1
        in  = 1;
        // give FSM enough cycles to settle
        repeat (8) @(posedge clk);
        // now out should be 1 (state S1) if FSM works; bug keeps it at 0
        #1;  // step past edge to avoid NBA read race
        if (out !== 1'b1) begin
            $display("TEST_FAIL fsm_stuck");
        end else begin
            $display("TEST_PASS 1/1");
        end
        $finish;
    end

    // safety stop
    initial begin
        #1000;
        $display("TEST_FAIL timeout");
        $finish;
    end
endmodule
