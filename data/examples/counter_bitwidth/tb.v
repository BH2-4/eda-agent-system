`timescale 1ns/1ps
module tb;
    reg clk;
    reg rst;
    wire [7:0] count;

    counter dut(
        .clk(clk),
        .rst(rst),
        .count(count[3:0])
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer i;

    initial begin
        rst = 1;
        // hold reset for several edges
        repeat (3) @(posedge clk);
        #1 rst = 0;

        // run 200 posedges after reset deassert
        for (i = 1; i <= 200; i = i + 1) begin
            @(posedge clk);
            #1; // settle NBA
            if (count !== i[7:0]) begin
                $display("TEST_FAIL count_value");
                $finish;
            end
        end

        // sanity: after 200 increments count should be 200
        if (count !== 8'd200) begin
            $display("TEST_FAIL count_value");
            $finish;
        end

        $display("TEST_PASS 1/1");
        $finish;
    end
endmodule
