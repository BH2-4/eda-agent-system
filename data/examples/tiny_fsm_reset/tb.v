`timescale 1ns/1ps
module tb;
    reg clk;
    reg rst;
    reg in;
    wire out;

    integer i;

    tiny_fsm dut(
        .clk(clk),
        .rst(rst),
        .in(in),
        .out(out)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    initial begin
        // drive inputs during reset
        in  = 0;
        rst = 1;
        // hold reset for a few cycles (>=2)
        repeat (3) @(posedge clk);
        // deassert reset on inactive edge to avoid race
        @(negedge clk);
        rst = 0;

        // sample one tick after reset released
        @(posedge clk);
        #1;
        if (out !== 1'b0) begin
            $display("TEST_FAIL fsm_reset_state");
            $finish;
        end

        // additional: also confirm behaviour with a stable input of 0 stays at S0
        in = 0;
        repeat (4) @(posedge clk);
        #1;
        if (out !== 1'b0) begin
            $display("TEST_FAIL fsm_reset_state");
            $finish;
        end

        $display("TEST_PASS 1/1");
        $finish;
    end
endmodule
